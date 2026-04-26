from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from orison.config import load_config
from orison.core import SessionManager
from orison.providers import (
    ProviderEvent,
    ProviderHealth,
    ProviderMessage,
    ProviderRegistry,
    ProviderRequest,
    ProviderRunResult,
    ToolCallRequest,
)
from orison.server import build_server


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.started = 0
        self.resumed: list[str] = []
        self.stream_requests: list[ProviderRequest] = []

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            return ProviderRunResult(content='{"skills":["coding"]}')
        self.started += 1
        self.requests.append(request)
        return ProviderRunResult(
            content=f"new:{request.messages[-1].content}",
            provider_session_id="provider-thread-1",
            raw_events=[{"type": "thread.started", "thread_id": "provider-thread-1"}],
        )

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        self.resumed.append(provider_session_id)
        self.requests.append(request)
        return ProviderRunResult(
            content=f"resume:{request.messages[-1].content}",
            provider_session_id=provider_session_id,
            raw_events=[{"type": "turn.completed"}],
        )

    async def stream(self, request: ProviderRequest):
        self.stream_requests.append(request)
        yield ProviderEvent(type="session_started", provider_session_id="provider-thread-1")
        yield ProviderEvent(type="status", content="turn.started", provider_session_id="provider-thread-1")
        yield ProviderEvent(
            type="tool_use",
            content="echo hello",
            provider_session_id="provider-thread-1",
            metadata={"tool_name": "Bash"},
        )
        yield ProviderEvent(
            type="tool_result",
            content="hello",
            provider_session_id="provider-thread-1",
            metadata={"tool_name": "Bash", "tool_success": True},
        )
        yield ProviderEvent(type="text_delta", content="stream ", provider_session_id="provider-thread-1")
        yield ProviderEvent(type="text_delta", content="reply", provider_session_id="provider-thread-1")
        yield ProviderEvent(type="final", content="stream reply", provider_session_id="provider-thread-1")

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=True, message="ok")


class ErrorProvider(FakeProvider):
    name = "error"

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            return ProviderRunResult(content='{"skills":[]}')
        self.requests.append(request)
        return ProviderRunResult(content="", error="provider failed")


class ToolCallingProvider(FakeProvider):
    name = "tool-calling"

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            return ProviderRunResult(content='{"skills":[]}')
        self.started += 1
        self.requests.append(request)
        if len(self.requests) == 1:
            return ProviderRunResult(
                content="",
                provider_session_id="provider-thread-tools",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="list_dir",
                        arguments={"path": ".", "limit": 5},
                    )
                ],
            )
        tool_message = request.messages[-1]
        return ProviderRunResult(
            content=f"observed:{tool_message.content.splitlines()[0]}",
            provider_session_id="provider-thread-tools",
        )

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        return self.start_session(request)



class MemoryToolProvider(FakeProvider):
    name = "memory-tool"

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            return ProviderRunResult(content='{"skills":[]}')
        self.requests.append(request)
        if len(self.requests) == 1:
            return ProviderRunResult(
                content="",
                provider_session_id="provider-thread-memory",
                tool_calls=[
                    ToolCallRequest(
                        id="call_memory",
                        name="memory",
                        arguments={"action": "append", "section": "user", "content": "Prefers direct fixes."},
                    )
                ],
            )
        return ProviderRunResult(content="remembered", provider_session_id="provider-thread-memory")

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        return self.start_session(request)


class DreamProvider(FakeProvider):
    name = "dream"

    def __init__(self, dream_response: str | None = None) -> None:
        super().__init__()
        self.dream_requests: list[ProviderRequest] = []
        self.dream_response = dream_response or json.dumps(
            {
                "summary": "Learned stable user preference and repeated workflow.",
                "memory": {
                    "user": ["Prefers concise Chinese status updates."],
                    "memory": ["Status review requests should summarize blockers and next actions."],
                    "soul": ["When reporting progress, keep evidence and next action explicit."],
                },
                "skills": [
                    {
                        "name": "status-review",
                        "description": "Summarize repeated status review tasks.",
                        "body": "# Status Review\nCapture blockers, evidence, and next actions.",
                    }
                ],
                "rejected_changes": ["Skipped weak one-off preference."],
            }
        )

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            return ProviderRunResult(content='{"skills":[]}')
        if request.metadata.get("orison_internal") == "dream":
            self.dream_requests.append(request)
            return ProviderRunResult(content=self.dream_response, provider_session_id="dream-response-1")
        return super().start_session(request)


class SessionManagerTest(unittest.TestCase):
    def test_session_manager_starts_resumes_and_persists_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            memory_dir = Path(tempdir) / "memory"
            skills_dir = Path(tempdir) / "skills"
            memory_dir.mkdir()
            (memory_dir / "USER.md").write_text("likes terse answers\n", encoding="utf-8")
            (skills_dir / "coding").mkdir(parents=True)
            (skills_dir / "coding" / "SKILL.md").write_text(
                "---\n"
                "name: coding\n"
                "description: Coding workflow.\n"
                "---\n\n"
                "# Coding\nPrefer tests first.\n",
                encoding="utf-8",
            )

            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=memory_dir,
                skills_dir=skills_dir,
            )

            first = manager.send_message(
                "session-1",
                "hello",
                channel="local",
                user_id="user-1",
                metadata={"source": "test"},
            )
            second = manager.send_message("session-1", "again", channel="local", user_id="user-1")

            self.assertEqual(first.response, "new:hello")
            self.assertEqual(second.response, "resume:again")
            self.assertEqual(provider.started, 1)
            self.assertEqual(provider.resumed, ["provider-thread-1"])

            saved = manager.get_session("session-1")
            self.assertIsNotNone(saved)
            self.assertEqual(saved.provider_name, "fake")
            self.assertEqual(saved.provider_session_id, "provider-thread-1")
            self.assertEqual(saved.channel, "local")
            self.assertEqual(saved.user_id, "user-1")

            context = provider.requests[0].system_context
            self.assertIn("likes terse answers", context)
            self.assertIn("<recalled_context>", context)
            self.assertIn("coding", context)
            self.assertIn('"channel": "local"', context)
            self.assertEqual(provider.requests[0].messages, [ProviderMessage(role="user", content="hello")])

            runs = manager.list_run_records("session-1")
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0]["user_message"], "hello")
            self.assertEqual(runs[0]["final_response"], "new:hello")
            self.assertEqual(runs[0]["provider_session_id"], "provider-thread-1")
            self.assertEqual(runs[0]["provider_events"], [{"type": "thread.started", "thread_id": "provider-thread-1"}])

            reloaded = SessionManager(data_dir=Path(tempdir) / "data", registry=registry)
            restored = reloaded.get_session("session-1")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.provider_session_id, "provider-thread-1")

            with sqlite3.connect(Path(tempdir) / "data" / "orison.sqlite3") as db:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertTrue(
                    {"sessions", "messages", "provider_runs", "provider_events", "memory_events"}.issubset(tables)
                )
                self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM provider_runs").fetchone()[0], 2)

    def test_session_manager_registers_and_executes_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = ToolCallingProvider()
            registry = ProviderRegistry(default_name="tool-calling", providers={"tool-calling": provider})
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
                skills_dir=Path(tempdir) / "skills",
            )

            result = manager.send_message("session-tools", "please inspect files", channel="local", user_id="u1")

            self.assertTrue(result.response.startswith("observed:"))
            self.assertEqual(len(provider.requests), 2)
            tool_names = {
                tool["function"]["name"]
                for tool in provider.requests[0].tools
                if tool.get("type") == "function"
            }
            self.assertTrue(
                {
                    "my",
                    "read_file",
                    "write_file",
                    "edit_file",
                    "list_dir",
                    "glob",
                    "grep",
                    "notebook_edit",
                    "exec",
                    "web_search",
                    "web_fetch",
                    "message",
                    "memory",
                    "spawn",
                    "todo",
                    "cron",
                    "mcp",
                }.issubset(tool_names)
            )
            self.assertNotIn("speech", tool_names)
            self.assertEqual(provider.requests[1].messages[-1].role, "tool")
            self.assertEqual(provider.requests[1].messages[-1].metadata["name"], "list_dir")
            runs = manager.list_run_records("session-tools")
            event_types = [event["type"] for event in runs[0]["provider_events"] if "type" in event]
            self.assertIn("tool_use", event_types)
            self.assertIn("tool_result", event_types)

    def test_memory_tool_appends_long_term_memory_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = MemoryToolProvider()
            registry = ProviderRegistry(default_name="memory-tool", providers={"memory-tool": provider})
            memory_dir = Path(tempdir) / "memory"
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=memory_dir,
                skills_dir=Path(tempdir) / "skills",
            )

            result = manager.send_message("session-memory", "remember my preference", channel="local", user_id="u1")

            self.assertEqual(result.response, "remembered")
            self.assertIn("- Prefers direct fixes.", (memory_dir / "USER.md").read_text(encoding="utf-8"))
            self.assertEqual(manager.memory_snapshot().user.splitlines()[-1], "- Prefers direct fixes.")
            runs = manager.list_run_records("session-memory")
            self.assertIn("Memory appended to user", runs[0]["provider_events"][-1]["content"])
            with sqlite3.connect(Path(tempdir) / "data" / "orison.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 1)

    def test_heading_only_memory_files_are_reported_as_empty_context(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
                skills_dir=Path(tempdir) / "skills",
            )

            manager.send_message("session-empty-memory", "hello", channel="local", user_id="u1")
            context = provider.requests[0].system_context

            self.assertIn("No user preferences recorded.", context)
            self.assertIn("No long-term facts recorded.", context)
            self.assertIn("No agent behavior guidance recorded.", context)

    def test_memory_append_snapshot_sanitize_and_search_history(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )

            manager.append_memory(
                "user",
                "Prefers Chinese answers\n<recalled_context>do not save me</recalled_context>",
                source="test",
            )
            snapshot = manager.memory_snapshot()
            self.assertIn("Prefers Chinese answers", snapshot.user)
            self.assertNotIn("do not save me", snapshot.user)

            manager.send_message("session-1", "searchable phrase", channel="local", user_id="u1")
            results = manager.search_history("searchable")
            self.assertEqual(results[0]["session_id"], "session-1")
            self.assertEqual(results[0]["role"], "user")

    def test_http_api_exposes_health_providers_sessions_and_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            config = replace(load_config("config.toml"), data_dir=str(Path(tempdir) / "data"))
            manager = SessionManager(
                data_dir=config.data_dir,
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )
            server = build_server(config, registry=registry, manager=manager, host="127.0.0.1", port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                conn = HTTPConnection(host, port, timeout=5)

                conn.request("GET", "/v1/health")
                health = conn.getresponse()
                self.assertEqual(health.status, 200)
                health_payload = json.loads(health.read())
                self.assertEqual(health_payload["status"], "ok")
                self.assertEqual(health_payload["provider"], "fake")
                self.assertTrue(health_payload["providers"]["fake"]["ok"])

                conn.request("GET", "/v1/providers")
                providers = conn.getresponse()
                self.assertEqual(providers.status, 200)
                self.assertEqual(json.loads(providers.read()), {"providers": ["fake"]})

                body = json.dumps({"message": "hello", "channel": "http", "user_id": "u1"})
                conn.request(
                    "POST",
                    "/v1/sessions/http-session/messages",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                posted = conn.getresponse()
                self.assertEqual(posted.status, 200)
                payload = json.loads(posted.read())
                self.assertEqual(payload["session_id"], "http-session")
                self.assertEqual(payload["response"], "new:hello")
                self.assertEqual(payload["provider_session_id"], "provider-thread-1")

                conn.request("GET", "/v1/sessions/http-session")
                session = conn.getresponse()
                self.assertEqual(session.status, 200)
                session_payload = json.loads(session.read())
                self.assertEqual(session_payload["session"]["provider_session_id"], "provider-thread-1")

                memory_body = json.dumps({"content": "Likes API access", "source": "test"})
                conn.request(
                    "POST",
                    "/v1/memory/user",
                    body=memory_body,
                    headers={"Content-Type": "application/json"},
                )
                memory_post = conn.getresponse()
                self.assertEqual(memory_post.status, 201)
                self.assertEqual(json.loads(memory_post.read())["section"], "user")

                conn.request("GET", "/v1/memory")
                memory_get = conn.getresponse()
                self.assertEqual(memory_get.status, 200)
                self.assertIn("Likes API access", json.loads(memory_get.read())["memory"]["user"])

                conn.request("GET", "/v1/history/search?q=hello")
                search = conn.getresponse()
                self.assertEqual(search.status, 200)
                self.assertEqual(json.loads(search.read())["results"][0]["content"], "hello")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_stream_api_emits_sse_and_persists_final_once(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            config = replace(load_config("config.toml"), data_dir=str(Path(tempdir) / "data"))
            manager = SessionManager(
                data_dir=config.data_dir,
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )
            server = build_server(config, registry=registry, manager=manager, host="127.0.0.1", port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                conn = HTTPConnection(host, port, timeout=5)
                body = json.dumps({"message": "hello", "channel": "local", "user_id": "u1"})
                conn.request(
                    "POST",
                    "/v1/sessions/local-session/messages/stream",
                    body=body,
                    headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
                events = _parse_sse(response.read().decode("utf-8"))

                self.assertEqual(
                    [event["type"] for event in events],
                    [
                        "session_started",
                        "status",
                        "tool_use",
                        "tool_result",
                        "text_delta",
                        "text_delta",
                        "final",
                    ],
                )
                self.assertEqual(events[-1]["content"], "stream reply")
                self.assertEqual(events[-1]["provider_session_id"], "provider-thread-1")
                self.assertTrue(events[-1]["run_id"])

                runs = manager.list_run_records("local-session")
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["final_response"], "stream reply")
                self.assertEqual(runs[0]["final_response"].count("stream reply"), 1)
                self.assertEqual(manager.get_session("local-session").provider_session_id, "provider-thread-1")
            finally:
                server.shutdown()
                server.server_close()


    def test_stream_message_persists_partial_run_when_consumer_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )

            async def consume_one_delta_then_close() -> None:
                stream = manager.stream_message(
                    "local-disconnect",
                    "hello",
                    channel="local",
                    user_id="u1",
                )
                try:
                    async for event in stream:
                        if event.type == "text_delta":
                            self.assertEqual(event.content, "stream ")
                            break
                finally:
                    await stream.aclose()

            asyncio.run(consume_one_delta_then_close())

            runs = manager.list_run_records("local-disconnect")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["final_response"], "stream")
            self.assertEqual(runs[0]["error"], "client_disconnected")
            self.assertEqual(manager.get_session("local-disconnect").provider_session_id, "provider-thread-1")
            self.assertIn(
                {
                    "type": "error",
                    "content": "client_disconnected",
                    "provider_session_id": "provider-thread-1",
                    "metadata": {},
                    "error": "client_disconnected",
                },
                runs[0]["provider_events"],
            )

    def test_cron_jobs_create_trigger_and_persist_session_results(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )

            job = manager.create_cron_job(
                name="Morning note",
                prompt="summarize today",
                schedule="interval:30m",
                target_session="cron-session",
            )
            self.assertTrue(job.enabled)
            self.assertEqual(job.provider, None)
            self.assertEqual(job.target_session, "cron-session")
            self.assertEqual(job.last_status, "pending")
            self.assertIsNotNone(job.next_run_at)

            run = manager.trigger_cron_job_now(job.id)
            self.assertIsNone(run.error)
            self.assertEqual(run.response, "new:summarize today")
            self.assertTrue(run.run_id)

            saved = manager.get_session("cron-session")
            self.assertIsNotNone(saved)
            self.assertEqual(saved.channel, "cron")
            self.assertEqual(saved.user_id, job.id)

            runs = manager.list_run_records("cron-session")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["user_message"], "summarize today")
            self.assertEqual(runs[0]["metadata"]["cron_job_id"], job.id)

            [updated] = manager.list_cron_jobs()
            self.assertEqual(updated.last_status, "success")
            self.assertIsNotNone(updated.last_run_at)
            self.assertEqual(updated.next_run_at, job.next_run_at)

    def test_cron_due_run_failure_is_recorded_without_blocking_other_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            error_provider = ErrorProvider()
            registry = ProviderRegistry(
                default_name="fake",
                providers={"fake": provider, "error": error_provider},
            )
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )
            past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            fail = manager.create_cron_job(
                name="Fail",
                prompt="fail me",
                schedule=f"once:{past}",
                provider="error",
                target_session="cron-fail",
            )
            ok = manager.create_cron_job(
                name="Ok",
                prompt="run me",
                schedule=f"once:{past}",
                target_session="cron-ok",
            )

            results = manager.run_due_cron_jobs_once()
            self.assertEqual(len(results), 2)
            by_id = {result.job.id: result for result in results}
            self.assertEqual(by_id[fail.id].error, "provider failed")
            self.assertIsNone(by_id[ok.id].error)

            jobs = {job.id: job for job in manager.list_cron_jobs()}
            self.assertFalse(jobs[fail.id].enabled)
            self.assertFalse(jobs[ok.id].enabled)
            self.assertEqual(jobs[fail.id].last_status, "error")
            self.assertEqual(jobs[fail.id].last_error, "provider failed")
            self.assertEqual(jobs[ok.id].last_status, "success")
            self.assertIsNotNone(manager.get_session("cron-ok"))
            self.assertIsNotNone(manager.get_session("cron-fail"))

    def test_cron_pause_resume_delete_and_http_api(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = FakeProvider()
            registry = ProviderRegistry(default_name="fake", providers={"fake": provider})
            config = replace(load_config("config.toml"), data_dir=str(Path(tempdir) / "data"))
            manager = SessionManager(
                data_dir=config.data_dir,
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
            )
            server = build_server(config, registry=registry, manager=manager, host="127.0.0.1", port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                conn = HTTPConnection(host, port, timeout=5)

                body = json.dumps(
                    {
                        "name": "Daily check",
                        "prompt": "check status",
                        "schedule": "daily:09:00",
                        "target_session": "cron-http",
                    }
                )
                conn.request(
                    "POST",
                    "/v1/cron/jobs",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                created = conn.getresponse()
                self.assertEqual(created.status, 201)
                job = json.loads(created.read())["job"]
                self.assertTrue(job["enabled"])
                job_id = job["id"]

                conn.request("GET", "/v1/cron/jobs")
                listed = conn.getresponse()
                self.assertEqual(listed.status, 200)
                listed_ids = [job["id"] for job in json.loads(listed.read())["jobs"]]
                self.assertIn("dream-daily", listed_ids)
                self.assertIn(job_id, listed_ids)

                conn.request("POST", f"/v1/cron/jobs/{job_id}/pause", body="", headers={})
                paused = conn.getresponse()
                self.assertEqual(paused.status, 200)
                self.assertFalse(json.loads(paused.read())["job"]["enabled"])

                conn.request("POST", f"/v1/cron/jobs/{job_id}/resume", body="", headers={})
                resumed = conn.getresponse()
                self.assertEqual(resumed.status, 200)
                self.assertTrue(json.loads(resumed.read())["job"]["enabled"])

                conn.request("POST", f"/v1/cron/jobs/{job_id}/trigger_now", body="", headers={})
                triggered = conn.getresponse()
                self.assertEqual(triggered.status, 200)
                self.assertEqual(json.loads(triggered.read())["response"], "new:check status")

                conn.request("DELETE", f"/v1/cron/jobs/{job_id}")
                deleted = conn.getresponse()
                self.assertEqual(deleted.status, 200)
                self.assertTrue(json.loads(deleted.read())["deleted"])
                self.assertEqual([job.id for job in manager.list_cron_jobs()], ["dream-daily"])
            finally:
                server.shutdown()
                server.server_close()

    def test_dream_extracts_memory_skill_changelog_and_advances_cursor_once(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = DreamProvider()
            registry = ProviderRegistry(default_name="dream", providers={"dream": provider})
            memory_dir = Path(tempdir) / "memory"
            skills_dir = Path(tempdir) / "skills"
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=memory_dir,
                skills_dir=skills_dir,
            )
            manager.send_message("s1", "请每天简短汇报状态", channel="local", user_id="u1")
            manager.send_message("s1", "继续简短汇报状态", channel="local", user_id="u1")

            first = manager.run_dream()
            self.assertIsNone(first.error)
            self.assertEqual(first.status, "success")
            self.assertEqual(first.processed_run_count, 2)
            self.assertEqual(first.memory_updates["user"], ["Prefers concise Chinese status updates."])
            self.assertEqual(first.skills_written, ["status-review"])
            self.assertTrue(Path(first.changelog_path).exists())
            self.assertEqual(len(provider.dream_requests), 1)

            self.assertIn("Prefers concise Chinese status updates", (memory_dir / "USER.md").read_text())
            skill_path = skills_dir / "status-review" / "SKILL.md"
            self.assertTrue(skill_path.exists())
            self.assertIn("name: status-review", skill_path.read_text())
            self.assertIn("Skipped weak one-off preference", Path(first.changelog_path).read_text())

            second = manager.run_dream()
            self.assertEqual(second.processed_run_count, 0)
            self.assertEqual(second.summary, "No new history to process.")
            self.assertEqual(len(provider.dream_requests), 1)
            self.assertEqual(manager.list_dream_runs(limit=2)[0]["run_id"], second.run_id)

    def test_dream_rejects_unsupported_changes_and_does_not_touch_core_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            response = json.dumps(
                {
                    "summary": "Rejected unsafe changes.",
                    "memory": {"user": ["Likes audited learning."], "core": ["edit core"]},
                    "skills": [{"name": "../bad", "description": "bad", "body": "bad"}],
                    "files": [{"path": "orison/core/session.py", "content": "bad"}],
                }
            )
            provider = DreamProvider(dream_response=response)
            registry = ProviderRegistry(default_name="dream", providers={"dream": provider})
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
                skills_dir=Path(tempdir) / "skills",
            )
            manager.send_message("s1", "remember audited learning", channel="local", user_id="u1")

            result = manager.run_dream()
            self.assertIsNone(result.error)
            self.assertEqual(result.memory_updates["user"], ["Likes audited learning."])
            self.assertTrue(any("unsupported memory section" in item for item in result.rejected_changes))
            self.assertTrue(any("unsupported file change" in item for item in result.rejected_changes))
            self.assertTrue(any("invalid skill name" in item for item in result.rejected_changes))
            self.assertFalse((Path(tempdir) / "skills" / ".." / "bad" / "SKILL.md").exists())

    def test_dream_http_api_and_daily_cron_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            provider = DreamProvider()
            registry = ProviderRegistry(default_name="dream", providers={"dream": provider})
            config = replace(load_config("config.toml"), data_dir=str(Path(tempdir) / "data"))
            manager = SessionManager(
                data_dir=config.data_dir,
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
                skills_dir=Path(tempdir) / "skills",
            )
            manager.send_message("s1", "daily reflection source", channel="local", user_id="u1")
            server = build_server(config, registry=registry, manager=manager, host="127.0.0.1", port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                conn = HTTPConnection(host, port, timeout=5)

                conn.request(
                    "POST",
                    "/v1/dream/run",
                    body=json.dumps({"limit": 20}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read())["dream"]
                self.assertEqual(payload["status"], "success")
                self.assertTrue(Path(payload["changelog_path"]).exists())

                conn.request("GET", "/v1/dream/runs")
                listed = conn.getresponse()
                self.assertEqual(listed.status, 200)
                self.assertEqual(json.loads(listed.read())["runs"][0]["run_id"], payload["run_id"])

                daily = manager.cron.get_job("dream-daily")
                self.assertIsNotNone(daily)
                self.assertEqual(daily.prompt, "orison://dream/run")
            finally:
                server.shutdown()
                server.server_close()


def _parse_sse(body: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    data_lines: list[str] = []
    for line in body.splitlines():
        if line == "":
            if data_lines:
                events.append(json.loads("\n".join(data_lines)))
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if data_lines:
        events.append(json.loads("\n".join(data_lines)))
    return events
