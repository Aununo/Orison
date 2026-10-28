"""Core HTTP server for the Orison MVP."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from orison.config import OrisonConfig, load_config
from orison.limits import StorageLimitError
from orison.core import SessionManager, SessionRecord
from orison.cron import DEFAULT_DREAM_SCHEDULE, cron_job_payload, cron_run_payload
from orison.providers.registry import ProviderRegistry, build_provider_registry
from orison.timezone import BOT_TIMEZONE


logger = logging.getLogger(__name__)

_DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024


class OrisonHTTPServer(ThreadingHTTPServer):
    def server_close(self) -> None:
        stop = getattr(self, "cron_stop", None)
        if isinstance(stop, threading.Event):
            stop.set()
        for name in ("cron_thread", "spontaneous_thread"):
            thread = getattr(self, name, None)
            if isinstance(thread, threading.Thread):
                thread.join(timeout=2)
        super().server_close()


class OrisonHandler(BaseHTTPRequestHandler):
    server_version = "OrisonCore/0.1"

    def do_GET(self) -> None:
        if not self._authorize_request():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/v1/health":
            provider_health = _provider_health_payload(self.server.registry)  # type: ignore[attr-defined]
            status = "ok" if provider_health.get(self.server.default_provider, {}).get("ok") else "degraded"  # type: ignore[attr-defined]
            self._send_json(
                {
                    "status": status,
                    "provider": self.server.default_provider,  # type: ignore[attr-defined]
                    "providers": provider_health,
                }
            )
            return
        if path == "/v1/providers":
            self._send_json({"providers": self.server.provider_names})  # type: ignore[attr-defined]
            return
        if path == "/v1/cron/jobs":
            jobs = self.server.manager.list_cron_jobs()  # type: ignore[attr-defined]
            self._send_json({"jobs": [cron_job_payload(job) for job in jobs]})
            return
        if path == "/v1/dream/runs":
            try:
                limit = int(_first_query_value(parsed.query, "limit") or "20")
                runs = self.server.manager.list_dream_runs(limit=limit)  # type: ignore[attr-defined]
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"runs": runs})
            return
        if path == "/v1/memory":
            snapshot = self.server.manager.memory_snapshot()  # type: ignore[attr-defined]
            self._send_json({"memory": snapshot.as_dict()})
            return
        if path == "/v1/history/search":
            query = _first_query_value(parsed.query, "q")
            if not query:
                self._send_json({"error": "q must be a non-empty query string"}, status=400)
                return
            try:
                limit = int(_first_query_value(parsed.query, "limit") or "20")
                results = self.server.manager.search_history(query, limit=limit)  # type: ignore[attr-defined]
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"results": results})
            return
        session_id = _match_session_get(path)
        if session_id is not None:
            session = self.server.manager.get_session(session_id)  # type: ignore[attr-defined]
            if session is None:
                self._send_json({"error": "session not found"}, status=404)
                return
            self._send_json({"session": _session_payload(session)})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if not self._authorize_request():
            return
        path = urlparse(self.path).path
        if path == "/v1/cron/jobs":
            self._handle_create_cron_job()
            return

        if path == "/v1/dream/run":
            self._handle_dream_run()
            return

        cron_action = _match_cron_job_action(path)
        if cron_action is not None:
            self._handle_cron_job_action(*cron_action)
            return

        memory_section = _match_memory_append(path)
        if memory_section is not None:
            try:
                payload = self._read_json_body()
                content = payload.get("content", payload.get("text"))
                if not isinstance(content, str) or not content.strip():
                    self._send_json({"error": "content must be a non-empty string"}, status=400)
                    return
                source = _optional_str(payload.get("source"), "manual")
                metadata = payload.get("metadata", {})
                if not isinstance(metadata, dict):
                    self._send_json({"error": "metadata must be an object"}, status=400)
                    return
                appended = self.server.manager.append_memory(  # type: ignore[attr-defined]
                    memory_section,
                    content,
                    source=source,
                    metadata=metadata,
                )
            except json.JSONDecodeError:
                self._send_json({"error": "request body must be JSON"}, status=400)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except StorageLimitError as exc:
                self._send_json({"error": str(exc)}, status=507)
                return

            self._send_json({"section": memory_section, "content": appended}, status=201)
            return

        stream_session_id = _match_session_message_stream(path)
        if stream_session_id is not None:
            self._handle_stream_message(stream_session_id)
            return

        session_id = _match_session_message(path)
        if session_id is None:
            self._send_json({"error": "not found"}, status=404)
            return

        try:
            payload = self._read_json_body()
            message = payload.get("message", payload.get("text"))
            if not isinstance(message, str) or not message.strip():
                self._send_json({"error": "message must be a non-empty string"}, status=400)
                return
            channel = _optional_str(payload.get("channel"), "http")
            user_id = _optional_str(payload.get("user_id"), "")
            metadata = payload.get("metadata", {})
            if not isinstance(metadata, dict):
                self._send_json({"error": "metadata must be an object"}, status=400)
                return
            provider = payload.get("provider")
            if provider is not None and not isinstance(provider, str):
                self._send_json({"error": "provider must be a string"}, status=400)
                return

            observe_only = payload.get("observe_only", False)
            if not isinstance(observe_only, bool):
                raise ValueError("observe_only must be a boolean")
            result = self.server.manager.send_message(  # type: ignore[attr-defined]
                session_id,
                message,
                channel=channel,
                user_id=user_id,
                metadata=metadata,
                provider_name=provider,
                observe_only=observe_only,
            )
        except json.JSONDecodeError:
            self._send_json({"error": "request body must be JSON"}, status=400)
            return
        except (KeyError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=403)
            return
        except StorageLimitError as exc:
            self._send_json({"error": str(exc)}, status=507)
            return
        except sqlite3.OperationalError as exc:
            self._send_json({"error": str(exc)}, status=507 if "full" in str(exc).lower() else 500)
            return

        self._send_json(
            {
                "session_id": result.session.session_id,
                "response": result.response,
                "error": result.error,
                "provider_session_id": result.provider_session_id,
                "run_id": result.run_id,
            },
            status=500 if result.error else 200,
        )

    def do_DELETE(self) -> None:
        if not self._authorize_request():
            return
        path = urlparse(self.path).path
        job_id = _match_cron_job_delete(path)
        if job_id is None:
            self._send_json({"error": "not found"}, status=404)
            return
        deleted = self.server.manager.delete_cron_job(job_id)  # type: ignore[attr-defined]
        if not deleted:
            self._send_json({"error": "cron job not found"}, status=404)
            return
        self._send_json({"deleted": True, "id": job_id})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_stream_message(self, session_id: str) -> None:
        try:
            payload = self._read_json_body()
            message = payload.get("message", payload.get("text"))
            if not isinstance(message, str) or not message.strip():
                self._send_json({"error": "message must be a non-empty string"}, status=400)
                return
            channel = _optional_str(payload.get("channel"), "http")
            user_id = _optional_str(payload.get("user_id"), "")
            metadata = payload.get("metadata", {})
            if not isinstance(metadata, dict):
                self._send_json({"error": "metadata must be an object"}, status=400)
                return
            provider = payload.get("provider")
            if provider is not None and not isinstance(provider, str):
                self._send_json({"error": "provider must be a string"}, status=400)
                return
            observe_only = payload.get("observe_only", False)
            if not isinstance(observe_only, bool):
                raise ValueError("observe_only must be a boolean")
        except json.JSONDecodeError:
            self._send_json({"error": "request body must be JSON"}, status=400)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        async def write_stream() -> None:
            try:
                async for event in self.server.manager.stream_message(  # type: ignore[attr-defined]
                    session_id,
                    message,
                    channel=channel,
                    user_id=user_id,
                    metadata=metadata,
                    provider_name=provider,
                    observe_only=observe_only,
                ):
                    self._send_sse(event.type, _provider_event_payload(session_id, event))
            except (BrokenPipeError, ConnectionResetError):
                return
            except (StorageLimitError, PermissionError, ValueError, sqlite3.OperationalError) as exc:
                self._send_sse("error", {"type": "error", "session_id": session_id, "content": str(exc), "error": str(exc)})

        asyncio.run(write_stream())
        self.close_connection = True

    def _handle_create_cron_job(self) -> None:
        try:
            payload = self._read_json_body()
            name = payload.get("name")
            prompt = payload.get("prompt")
            schedule = payload.get("schedule")
            if not isinstance(name, str) or not isinstance(prompt, str) or not isinstance(schedule, str):
                self._send_json({"error": "name, prompt, and schedule must be strings"}, status=400)
                return
            provider = payload.get("provider")
            if provider is not None and not isinstance(provider, str):
                self._send_json({"error": "provider must be a string"}, status=400)
                return
            target_session = payload.get("target_session")
            if target_session is not None and not isinstance(target_session, str):
                self._send_json({"error": "target_session must be a string"}, status=400)
                return
            enabled = payload.get("enabled", True)
            if not isinstance(enabled, bool):
                self._send_json({"error": "enabled must be a boolean"}, status=400)
                return
            job = self.server.manager.create_cron_job(  # type: ignore[attr-defined]
                name=name,
                prompt=prompt,
                schedule=schedule,
                provider=provider,
                target_session=target_session,
                enabled=enabled,
            )
        except json.JSONDecodeError:
            self._send_json({"error": "request body must be JSON"}, status=400)
            return
        except (KeyError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"job": cron_job_payload(job)}, status=201)

    def _handle_cron_job_action(self, job_id: str, action: str) -> None:
        try:
            if action == "pause":
                job = self.server.manager.pause_cron_job(job_id)  # type: ignore[attr-defined]
                self._send_json({"job": cron_job_payload(job)})
                return
            if action == "resume":
                job = self.server.manager.resume_cron_job(job_id)  # type: ignore[attr-defined]
                self._send_json({"job": cron_job_payload(job)})
                return
            if action == "trigger_now":
                result = self.server.manager.trigger_cron_job_now(job_id)  # type: ignore[attr-defined]
                self._send_json(cron_run_payload(result), status=500 if result.error else 200)
                return
        except KeyError:
            self._send_json({"error": "cron job not found"}, status=404)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "not found"}, status=404)

    def _handle_dream_run(self) -> None:
        try:
            payload = self._read_json_body()
            provider = payload.get("provider")
            if provider is not None and not isinstance(provider, str):
                self._send_json({"error": "provider must be a string"}, status=400)
                return
            limit = payload.get("limit", 200)
            if not isinstance(limit, int) or isinstance(limit, bool):
                self._send_json({"error": "limit must be an integer"}, status=400)
                return
            result = self.server.manager.run_dream(provider_name=provider, limit=limit)  # type: ignore[attr-defined]
        except json.JSONDecodeError:
            self._send_json({"error": "request body must be JSON"}, status=400)
            return
        except (KeyError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except StorageLimitError as exc:
            self._send_json({"error": str(exc)}, status=507)
            return

        self._send_json({"dream": result.as_dict()}, status=500 if result.error else 200)

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        logger.info("%s %s -> %s", self.command, self.path, status)

    def _send_sse(self, event_name: str, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).splitlines() or [""]
        self.wfile.write(f"event: {event_name}\n".encode("utf-8"))
        for line in body:
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _authorize_request(self) -> bool:
        token = getattr(self.server, "api_token", "")  # type: ignore[attr-defined]
        if not token:
            return True
        expected = f"Bearer {token}"
        if self.headers.get("Authorization") == expected:
            return True
        self._send_json({"error": "unauthorized"}, status=401)
        return False

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        max_body_bytes = getattr(self.server, "max_body_bytes", _DEFAULT_MAX_BODY_BYTES)  # type: ignore[attr-defined]
        if length > max_body_bytes:
            raise ValueError(f"request body too large (max {max_body_bytes} bytes)")
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload


def build_server(
    config: OrisonConfig,
    *,
    registry: ProviderRegistry | None = None,
    manager: SessionManager | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    registry = registry or build_provider_registry(config)
    manager = manager or SessionManager(data_dir=config.data_dir, registry=registry, family=config.family,
                                        spontaneous=config.spontaneous)
    server = OrisonHTTPServer((host, port), OrisonHandler)
    server.api_token = os.environ.get("ORISON_API_TOKEN", "").strip()  # type: ignore[attr-defined]
    server.max_body_bytes = _max_body_bytes_from_env()  # type: ignore[attr-defined]
    server.default_provider = registry.default_name  # type: ignore[attr-defined]
    server.provider_names = registry.names()  # type: ignore[attr-defined]
    server.registry = registry  # type: ignore[attr-defined]
    server.manager = manager  # type: ignore[attr-defined]
    if os.environ.get("ORISON_DREAM_DAILY", "1") != "0":
        manager.ensure_daily_dream_cron(
            schedule=os.environ.get("ORISON_DREAM_DAILY_SCHEDULE", DEFAULT_DREAM_SCHEDULE),
        )
    _start_cron_scheduler(server)
    return server


def main() -> None:
    os.environ["TZ"] = BOT_TIMEZONE.key
    if hasattr(time, "tzset"):
        time.tzset()
    logging.basicConfig(
        level=os.environ.get("ORISON_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config_path = os.environ.get("ORISON_CONFIG", "config.toml")
    config = load_config(config_path)

    host = os.environ.get("ORISON_HOST", "127.0.0.1")
    port = int(os.environ.get("ORISON_PORT", "8765"))
    if not _is_loopback_host(host) and not os.environ.get("ORISON_API_TOKEN", "").strip():
        raise RuntimeError("ORISON_API_TOKEN is required when ORISON_HOST is not a loopback address")
    server = build_server(config, host=host, port=port)

    print(f"Orison Core listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Orison Core stopped", flush=True)
    finally:
        server.server_close()


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _max_body_bytes_from_env() -> int:
    raw = os.environ.get("ORISON_MAX_BODY_BYTES")
    if not raw:
        return _DEFAULT_MAX_BODY_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_BODY_BYTES
    return max(1, min(value, 64 * 1024 * 1024))


def _match_session_get(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["v1", "sessions"]:
        return parts[2]
    return None


def _match_cron_job_action(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[:3] == ["v1", "cron", "jobs"]:
        return parts[3], parts[4]
    return None


def _match_cron_job_delete(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:3] == ["v1", "cron", "jobs"]:
        return parts[3]
    return None


def _match_session_message(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "messages":
        return parts[2]
    return None


def _match_session_message_stream(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[:2] == ["v1", "sessions"] and parts[3:] == ["messages", "stream"]:
        return parts[2]
    return None


def _match_memory_append(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["v1", "memory"]:
        return parts[2]
    return None


def _session_payload(session: SessionRecord) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "provider_name": session.provider_name,
        "provider_session_id": session.provider_session_id,
        "channel": session.channel,
        "user_id": session.user_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _provider_event_payload(session_id: str, event: Any) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": event.type,
        "session_id": session_id,
        "content": event.content,
        "metadata": event.metadata,
    }
    if event.provider_session_id:
        payload["provider_session_id"] = event.provider_session_id
    if event.error:
        payload["error"] = event.error
    run_id = event.metadata.get("run_id") if isinstance(event.metadata, dict) else None
    if isinstance(run_id, str):
        payload["run_id"] = run_id
    return payload


def _provider_health_payload(registry: ProviderRegistry) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for name in registry.names():
        try:
            health = registry.get(name).healthcheck()
            payload[name] = {"ok": health.ok, "message": health.message}
        except Exception as exc:
            payload[name] = {"ok": False, "message": str(exc)}
    return payload


def _optional_str(value: object, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("expected string value")
    return value


def _first_query_value(query: str, key: str) -> str:
    values = parse_qs(query).get(key, [])
    return values[0] if values else ""


def _start_cron_scheduler(server: OrisonHTTPServer) -> None:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(float(os.environ.get("ORISON_CRON_POLL_SECONDS", "1"))):
            try:
                server.manager.run_due_cron_jobs_once()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Scheduled maintenance failed")
                continue

    def spontaneous_loop() -> None:
        # Separate from cron so a slow model turn never delays reminders.
        while not stop.wait(5):
            try:
                server.manager.run_spontaneous_once()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Spontaneous reply check failed")

    thread = threading.Thread(target=loop, name="orison-cron", daemon=True)
    spontaneous_thread = threading.Thread(target=spontaneous_loop, name="orison-spontaneous", daemon=True)
    server.cron_stop = stop  # type: ignore[attr-defined]
    server.cron_thread = thread  # type: ignore[attr-defined]
    server.spontaneous_thread = spontaneous_thread  # type: ignore[attr-defined]
    thread.start()
    spontaneous_thread.start()


if __name__ == "__main__":
    main()
