"""OpenAI Codex OAuth Responses API provider boundary.

This provider deliberately does **not** launch `codex exec`. It only reuses the
Codex/ChatGPT OAuth login state through oauth-cli-kit, then calls the Codex
Responses API directly. This keeps Codex CLI's agent runtime, project rules, and
CLI session context out of Orison chat turns.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess  # kept as a module attribute so tests can assert it is not used
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

import httpx

from orison.config import ProviderConfig
from orison.providers.base import (
    ProviderEvent,
    ProviderHealth,
    ProviderMessage,
    ProviderRequest,
    ProviderRunResult,
    ToolCallRequest,
)

try:  # pragma: no cover - exercised via provider health/error when dependency is absent
    from oauth_cli_kit import get_token as get_codex_token
except Exception:  # pragma: no cover
    def get_codex_token():  # type: ignore[no-redef]
        raise RuntimeError("oauth-cli-kit is not installed; run `pip install oauth-cli-kit`")


logger = logging.getLogger(__name__)

DEFAULT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_ORIGINATOR = "orison"


class OpenAICodexProvider:
    name = "openai-codex"

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        return self._run(provider_session_id=None, request=request)

    def resume_session(
        self,
        provider_session_id: str,
        request: ProviderRequest,
    ) -> ProviderRunResult:
        # The OAuth Responses API path does not resume Codex CLI agent sessions.
        # Keep the prior response id only as Orison's provider_session_id marker;
        # Orison owns memory/history injection independently of the provider.
        return self._run(provider_session_id=provider_session_id, request=request)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        try:
            token = await asyncio.to_thread(get_codex_token)
            body = self._build_body(request)
        except Exception as exc:
            yield ProviderEvent(type="error", content=_auth_error_message(exc), error=_auth_error_message(exc))
            return

        headers = _build_headers(str(token.account_id or ""), str(token.access))
        final_parts: list[str] = []
        current_provider_session_id = _metadata_provider_session_id(request)

        async def on_event(raw: dict[str, Any]) -> None:
            nonlocal current_provider_session_id
            response_id = _response_id(raw)
            if response_id:
                current_provider_session_id = response_id

        async def event_streamer() -> AsyncIterator[ProviderEvent]:
            nonlocal current_provider_session_id

            async def handle_raw(raw: dict[str, Any]) -> None:
                nonlocal current_provider_session_id
                response_id = _response_id(raw)
                if response_id and response_id != current_provider_session_id:
                    current_provider_session_id = response_id
                    pending.append(
                        ProviderEvent(type="session_started", provider_session_id=current_provider_session_id, raw=raw)
                    )
                event_type = str(raw.get("type", ""))
                if event_type == "response.output_text.delta":
                    delta = str(raw.get("delta") or "")
                    if delta:
                        final_parts.append(delta)
                        pending.append(
                            ProviderEvent(
                                type="text_delta",
                                content=delta,
                                provider_session_id=current_provider_session_id,
                                raw=raw,
                            )
                        )
                elif event_type in {"error", "response.failed"}:
                    message = _event_error(raw)
                    pending.append(
                        ProviderEvent(
                            type="error",
                            content=message,
                            provider_session_id=current_provider_session_id,
                            raw=raw,
                            error=message,
                        )
                    )

            pending: list[ProviderEvent] = []

            async def collect_raw(raw: dict[str, Any]) -> None:
                await handle_raw(raw)

            try:
                content, raw_events, _usage, response_id, error, tool_calls = await _request_codex(
                    DEFAULT_CODEX_URL,
                    headers,
                    body,
                    on_event=collect_raw,
                )
                for event in pending:
                    yield event
                current_provider_session_id = response_id or current_provider_session_id
                if error:
                    yield ProviderEvent(
                        type="error",
                        content=error,
                        provider_session_id=current_provider_session_id,
                        raw=raw_events[-1] if raw_events else None,
                        error=error,
                    )
                    return
                final = content or "".join(final_parts)
                artifacts = _extract_image_artifacts(raw_events)
                if tool_calls:
                    yield ProviderEvent(
                        type="tool_use",
                        content=", ".join(tool.name for tool in tool_calls),
                        provider_session_id=current_provider_session_id,
                        metadata={"tool_calls": [tool.to_openai_tool_call() for tool in tool_calls]},
                    )
                final_metadata: dict[str, Any] = {}
                if tool_calls:
                    final_metadata["tool_calls"] = [tool.to_openai_tool_call() for tool in tool_calls]
                if artifacts:
                    final_metadata["images"] = artifacts
                yield ProviderEvent(
                    type="final",
                    content=final,
                    provider_session_id=current_provider_session_id,
                    raw=raw_events[-1] if raw_events else None,
                    metadata=final_metadata,
                )
            except Exception as exc:
                message = _request_error_message(exc)
                yield ProviderEvent(
                    type="error",
                    content=message,
                    provider_session_id=current_provider_session_id,
                    error=message,
                )

        async for event in event_streamer():
            yield event

    def healthcheck(self) -> ProviderHealth:
        try:
            token = get_codex_token()
        except Exception as exc:
            return ProviderHealth(ok=False, message=_auth_error_message(exc))
        if not getattr(token, "access", None):
            return ProviderHealth(ok=False, message="Codex OAuth authentication failed: empty access token")
        return ProviderHealth(ok=True, message="Codex OAuth token is available")

    def _run(
        self,
        provider_session_id: str | None,
        request: ProviderRequest,
    ) -> ProviderRunResult:
        try:
            token = get_codex_token()
            headers = _build_headers(str(token.account_id or ""), str(token.access))
            body = self._build_body(request)
            content, raw_events, usage, response_id, error, tool_calls = _run_async(
                _request_codex(DEFAULT_CODEX_URL, headers, body)
            )
        except Exception as exc:
            message = _auth_error_message(exc) if _looks_like_auth_error(exc) else _request_error_message(exc)
            return ProviderRunResult(content="", provider_session_id=provider_session_id, error=message)

        artifacts = _extract_image_artifacts(raw_events)
        return ProviderRunResult(
            content=content,
            provider_session_id=response_id or provider_session_id,
            raw_events=raw_events,
            usage=usage,
            tool_calls=tool_calls,
            artifacts=artifacts,
            error=error,
        )

    def _build_body(self, request: ProviderRequest) -> dict[str, Any]:
        messages = list(request.messages)
        instructions = request.system_context or ""
        input_items = _convert_messages(messages)
        model = _strip_model_prefix(request.model or self.config.model)

        body: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": True,
            "instructions": instructions,
            "input": input_items,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": _prompt_cache_key(request),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        converted_tools = _convert_tools(request.tools) if request.tools else []
        if not request.metadata.get("orison_internal") and not any(tool.get("type") == "image_generation" for tool in converted_tools):
            converted_tools.append({"type": "image_generation"})
        if converted_tools:
            body["tools"] = converted_tools
        if self.config.reasoning_effort:
            body["reasoning"] = {"effort": self.config.reasoning_effort}
        if self.config.service_tier:
            # The Codex OAuth endpoint still requires the legacy name for Fast mode.
            body["service_tier"] = "priority" if self.config.service_tier == "fast" else self.config.service_tier
        return body


async def _request_codex(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], str | None, str | None, list[ToolCallRequest]]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code != 200:
                raw = await response.aread()
                return "", [], {}, None, _friendly_http_error(response.status_code, raw.decode("utf-8", "ignore")), []
            return await _consume_sse(response, on_event=on_event)


async def _consume_sse(
    response: httpx.Response,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], str | None, str | None, list[ToolCallRequest]]:
    content_parts: list[str] = []
    raw_events: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    response_id: str | None = None
    error: str | None = None
    tool_calls: list[ToolCallRequest] = []
    tool_call_buffers: dict[str, dict[str, str]] = {}
    completed = False

    async for raw in _iter_sse(response):
        raw_events.append(raw)
        if on_event is not None:
            await on_event(raw)
        response_id = _response_id(raw) or response_id
        event_type = str(raw.get("type", ""))
        if event_type == "response.output_text.delta":
            content_parts.append(str(raw.get("delta") or ""))
        elif event_type == "response.output_item.added":
            item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
            if item.get("type") == "function_call" and item.get("call_id"):
                tool_call_buffers[str(item["call_id"])] = {
                    "id": str(item.get("id") or "fc_0"),
                    "name": str(item.get("name") or ""),
                    "arguments": str(item.get("arguments") or ""),
                }
        elif event_type == "response.function_call_arguments.delta":
            call_id = str(raw.get("call_id") or "")
            if call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] += str(raw.get("delta") or "")
        elif event_type == "response.function_call_arguments.done":
            call_id = str(raw.get("call_id") or "")
            if call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] = str(raw.get("arguments") or "")
        elif event_type == "response.output_item.done":
            item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
            if item.get("type") == "function_call" and item.get("call_id"):
                call_id = str(item["call_id"])
                buffer = tool_call_buffers.get(call_id) or {}
                raw_args = str(buffer.get("arguments") or item.get("arguments") or "{}")
                tool_calls.append(
                    ToolCallRequest(
                        id=f"{call_id}|{buffer.get('id') or item.get('id') or 'fc_0'}",
                        name=str(buffer.get("name") or item.get("name") or ""),
                        arguments=_parse_tool_arguments(raw_args),
                    )
                )
        elif event_type == "response.completed":
            completed = True
            response_obj = raw.get("response") if isinstance(raw.get("response"), dict) else {}
            usage = response_obj.get("usage") if isinstance(response_obj.get("usage"), dict) else usage
        elif event_type in {"error", "response.failed"}:
            error = _event_error(raw)
        elif event_type == "response.incomplete":
            error = "Codex response was incomplete"

    if not completed and not error:
        error = "Codex response stream ended before completion"
    return "".join(content_parts), raw_events, usage, response_id, error, tool_calls


async def _iter_sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    buffer: list[str] = []

    def flush() -> dict[str, Any] | None:
        data_lines = [line[5:].strip() for line in buffer if line.startswith("data:")]
        buffer.clear()
        if not data_lines:
            return None
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return None
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("Ignored non-JSON Codex SSE payload: %s", data[:200])
            return None
        return parsed if isinstance(parsed, dict) else None

    async for line in response.aiter_lines():
        if line == "":
            event = flush() if buffer else None
            if event is not None:
                yield event
            continue
        buffer.append(line)
    if buffer:
        event = flush()
        if event is not None:
            yield event


def _convert_messages(messages: list[ProviderMessage]) -> list[dict[str, Any]]:
    input_items: list[dict[str, Any]] = []
    for idx, message in enumerate(messages):
        if message.role == "tool":
            call_id, _item_id = _split_tool_call_id(message.metadata.get("tool_call_id"))
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": message.content,
                }
            )
            continue
        role = message.role if message.role in {"user", "assistant"} else "user"
        content_type = "input_text" if role == "user" else "output_text"
        content_blocks: list[dict[str, Any]] = []
        if message.content:
            text = message.content
            if role == "user" and message.metadata.get("speaker_id"):
                speaker = {key: message.metadata[key] for key in ("speaker_id", "role", "name", "native_name", "timestamp") if key in message.metadata}
                text = "[Message author: " + json.dumps(speaker, ensure_ascii=True) + "]\n" + text
            content_blocks.append({"type": content_type, "text": text})
        if role == "user":
            content_blocks.extend(_image_content_blocks(message.metadata))
        if content_blocks:
            item: dict[str, Any] = {
                "type": "message",
                "role": role,
                "content": content_blocks,
            }
            if role == "assistant":
                item["status"] = "completed"
                item["id"] = f"msg_{idx}"
            input_items.append(item)
        if role == "assistant":
            for tool_call in message.metadata.get("tool_calls", []) or []:
                if not isinstance(tool_call, dict):
                    continue
                fn = tool_call.get("function")
                if not isinstance(fn, dict):
                    continue
                call_id, item_id = _split_tool_call_id(tool_call.get("id"))
                input_items.append(
                    {
                        "type": "function_call",
                        "id": item_id or f"fc_{idx}",
                        "call_id": call_id,
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
    return input_items or [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": ""}]}]



def _image_content_blocks(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    images = metadata.get("images")
    if not isinstance(images, list):
        return blocks
    for image in images:
        if not isinstance(image, dict):
            continue
        url = image.get("data") or image.get("url") or image.get("file")
        if not isinstance(url, str) or not url.startswith(("https://", "http://", "data:image/")):
            continue
        if isinstance(image.get("index"), int):
            blocks.append({"type": "input_text", "text": f"[Image {image['index']}]"})
        blocks.append({"type": "input_image", "image_url": url.strip()})
    return blocks



def _extract_image_artifacts(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for raw in raw_events:
        candidates: list[Any] = []
        item = raw.get("item")
        if isinstance(item, dict):
            candidates.append(item)
        output = raw.get("output")
        if isinstance(output, list):
            candidates.extend(obj for obj in output if isinstance(obj, dict))
        for obj in candidates:
            obj_type = str(obj.get("type") or "")
            if obj_type not in {"image_generation_call", "image", "output_image"}:
                continue
            result = obj.get("result") or obj.get("image") or obj.get("data")
            url = obj.get("url") or obj.get("image_url")
            if isinstance(result, str) and result.strip():
                data = result.strip()
                if not data.startswith("data:"):
                    data = "data:image/png;base64," + data
                artifacts.append({"type": "image", "mime_type": "image/png", "data": data})
            elif isinstance(url, str) and url.strip():
                artifacts.append({"type": "image", "url": url.strip()})
    return artifacts



def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        parameters = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": fn.get("description") or "",
                "parameters": parameters,
                # Responses may normalize omitted strict into all-required fields.
                # Preserve optional arguments unless the tool explicitly opts in.
                "strict": fn.get("strict") is True,
            }
        )
    return converted


def _split_tool_call_id(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str) and value:
        if "|" in value:
            call_id, item_id = value.split("|", 1)
            return call_id, item_id or None
        return value, None
    return "call_0", None


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _build_headers(account_id: str, token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "OpenAI-Beta": "responses=experimental",
        "originator": DEFAULT_ORIGINATOR,
        "User-Agent": "orison (python)",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _strip_model_prefix(model: str) -> str:
    if model.startswith("openai-codex/") or model.startswith("openai_codex/"):
        return model.split("/", 1)[1]
    return model


def _prompt_cache_key(request: ProviderRequest) -> str:
    # The key routes requests that share a prompt prefix to the same cache, so it must stay
    # stable across turns of one conversation instead of hashing per-request content.
    purpose = str(request.metadata.get("orison_internal") or "conversation")
    return hashlib.sha256(f"orison:{purpose}:{request.session_id or ''}".encode("utf-8")).hexdigest()


def _response_id(raw: dict[str, Any]) -> str | None:
    response_obj = raw.get("response") if isinstance(raw.get("response"), dict) else None
    if response_obj and response_obj.get("id"):
        return str(response_obj["id"])
    if raw.get("response_id"):
        return str(raw["response_id"])
    return None


def _event_error(raw: dict[str, Any]) -> str:
    detail = raw.get("error") or raw.get("message") or raw
    return f"Codex response failed: {str(detail)[:500]}"


def _metadata_provider_session_id(request: ProviderRequest) -> str | None:
    value = request.metadata.get("provider_session_id")
    return str(value) if value else None


def _looks_like_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in ("oauth", "credential", "token", "login", "auth", "401", "403"))


def _auth_error_message(exc: Exception) -> str:
    return (
        "Codex OAuth authentication failed. Sign in again with oauth-cli-kit "
        "(`login_oauth_interactive`) as the Core service account and the same "
        "OAUTH_CLI_KIT_TOKEN_PATH; Orison uses the OAuth token file, not `codex exec`. "
        f"Details: {exc}"
    )


def _request_error_message(exc: Exception) -> str:
    return f"Codex Responses API request failed: {exc}"


def _friendly_http_error(status_code: int, raw: str) -> str:
    if status_code in {401, 403}:
        return _auth_error_message(RuntimeError(f"HTTP {status_code}: {raw[:300]}"))
    if status_code == 429:
        return "Codex Responses API rate limit or quota exceeded. Please try again later."
    return f"Codex Responses API HTTP {status_code}: {raw[:500]}"


def _run_async(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()
    raise RuntimeError("OpenAICodexProvider synchronous call cannot run inside an active event loop")

