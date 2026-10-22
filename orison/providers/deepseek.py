"""DeepSeek Chat Completions API adapter with private environment credentials."""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from orison.config import ProviderConfig
from orison.providers.base import (
    ProviderEvent, ProviderHealth, ProviderRequest, ProviderRunResult, ToolCallRequest,
)


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def healthcheck(self) -> ProviderHealth:
        available = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
        return ProviderHealth(available, "DeepSeek API key is configured; connectivity is unverified" if available
                              else "DEEPSEEK_API_KEY is not configured")

    def _headers(self) -> dict[str, str]:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _body(self, request: ProviderRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system_context:
            messages.append({"role": "system", "content": request.system_context})
        for item in request.messages:
            content: Any = item.content
            if item.role == "user":
                author = {k: item.metadata[k] for k in ("speaker_id", "role", "name", "native_name", "timestamp") if k in item.metadata}
                if author:
                    content = "[Message author: " + json.dumps(author, ensure_ascii=True) + "]\n" + content
                images = item.metadata.get("images", [])
                image_parts = []
                for image in images if isinstance(images, list) else []:
                    if not isinstance(image, dict):
                        continue
                    url = image.get("data") or image.get("url") or image.get("file")
                    if isinstance(url, str) and url.startswith(("https://", "http://", "data:image/")):
                        if isinstance(image.get("index"), int):
                            image_parts.append({"type": "text", "text": f"[Image {image['index']}]"})
                        image_parts.append({"type": "image_url", "image_url": {"url": url}})
                if image_parts:
                    content = [{"type": "text", "text": content}, *image_parts]
            message = {"role": item.role, "content": content}
            if item.role == "assistant" and item.metadata.get("tool_calls"):
                message["tool_calls"] = item.metadata["tool_calls"]
            if item.role == "tool":
                message["tool_call_id"] = item.metadata["tool_call_id"]
            messages.append(message)
        body: dict[str, Any] = {
            "model": request.model or self.config.model, "messages": messages, "stream": stream,
            # Non-thinking mode accepts tool history from another model without reasoning_content.
            "thinking": {"type": "disabled"},
        }
        if request.tools:
            body["tools"] = request.tools
        return body

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        try:
            headers, body = self._headers(), self._body(request, stream=False)
            with httpx.Client(timeout=120.0, follow_redirects=False, trust_env=False) as client:
                response = client.post(DEEPSEEK_URL, headers=headers, json=body)
            if response.status_code != 200:
                return ProviderRunResult(content="", error=_http_error(response.status_code))
            return _result(response.json())
        except Exception as exc:
            return ProviderRunResult(content="", error=_request_error(exc))

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        # Chat Completions is stateless; Orison sends the full bounded context each time.
        return self.start_session(request)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        try:
            headers, body = self._headers(), self._body(request, stream=True)
            content, calls, response_id, finish = "", {}, None, None
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", DEEPSEEK_URL, headers=headers, json=body) as response:
                    if response.status_code != 200:
                        message = _http_error(response.status_code)
                        yield ProviderEvent(type="error", content=message, error=message)
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        if not data:
                            continue
                        chunk = json.loads(data)
                        if chunk.get("error"):
                            raise ValueError("Invalid DeepSeek stream")
                        response_id = chunk.get("id") or response_id
                        if response_id is not None and not isinstance(response_id, str):
                            raise ValueError("Invalid DeepSeek response ID")
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta", {})
                            text = delta.get("content") or ""
                            if text:
                                content += text
                                yield ProviderEvent(type="text_delta", content=text, provider_session_id=response_id)
                            for call in delta.get("tool_calls", []) or []:
                                buffered = calls.setdefault(call["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                                buffered["id"] = call.get("id") or buffered["id"]
                                function = call.get("function", {})
                                buffered["function"]["name"] += function.get("name") or ""
                                buffered["function"]["arguments"] += function.get("arguments") or ""
                            finish = choice.get("finish_reason") or finish
            result = _result({"id": response_id, "choices": [{"finish_reason": finish, "message": {
                "content": content, "tool_calls": [calls[index] for index in sorted(calls)],
            }}]})
            if result.error:
                yield ProviderEvent(type="error", content=result.error, error=result.error)
                return
            metadata = {"tool_calls": [call.to_openai_tool_call() for call in result.tool_calls]} if result.tool_calls else {}
            if result.tool_calls:
                yield ProviderEvent(type="tool_use", content=", ".join(call.name for call in result.tool_calls), metadata=metadata)
            yield ProviderEvent(type="final", content=result.content, provider_session_id=response_id, metadata=metadata)
        except Exception as exc:
            message = _request_error(exc)
            yield ProviderEvent(type="error", content=message, error=message)


def _result(payload: Any) -> ProviderRunResult:
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Invalid DeepSeek response")
    choice = payload["choices"][0]
    if choice.get("finish_reason") not in {"stop", "tool_calls"}:
        return ProviderRunResult(content="", error="DeepSeek response was incomplete or stopped before completion")
    message = choice["message"]
    response_id = payload.get("id")
    if response_id is not None and not isinstance(response_id, str):
        raise ValueError("Invalid DeepSeek response ID")
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("Invalid DeepSeek text")
    calls = []
    for call in message.get("tool_calls", []) or []:
        function = call["function"]
        arguments = json.loads(function["arguments"])
        if not isinstance(arguments, dict) or not isinstance(call["id"], str) or not call["id"] or not isinstance(function["name"], str) or not function["name"]:
            raise ValueError("Invalid DeepSeek tool call")
        calls.append(ToolCallRequest(id=call["id"], name=function["name"], arguments=arguments))
    if not content.strip() and not calls:
        raise ValueError("Empty DeepSeek response")
    return ProviderRunResult(content=content, provider_session_id=response_id,
                             usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {}, tool_calls=calls)


def _http_error(status: int) -> str:
    reason = {401: "API key is invalid or expired", 402: "account balance is insufficient",
              403: "API access is denied", 429: "rate limit or quota exceeded"}.get(status, "API request failed")
    return f"DeepSeek HTTP {status}: {reason}"


def _request_error(exc: Exception) -> str:
    if isinstance(exc, ValueError) and str(exc) == "DEEPSEEK_API_KEY is not configured":
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "DeepSeek request timed out"
    if isinstance(exc, httpx.TransportError):
        return "DeepSeek API connection failed"
    return "DeepSeek returned an invalid response or the request could not be prepared"
