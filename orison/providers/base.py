"""Small provider boundary used by the Core.

The rest of Orison should depend on the request, event, result, and health
objects in this module instead of provider-specific SDK or CLI behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable


ProviderEventType = Literal[
    "session_started",
    "text_delta",
    "status",
    "tool_use",
    "tool_result",
    "final",
    "error",
]


@dataclass(frozen=True)
class ProviderMessage:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_openai_tool_call(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(frozen=True)
class ProviderRequest:
    messages: list[ProviderMessage]
    system_context: str = ""
    session_id: str | None = None
    workspace: str | None = None
    model: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderEvent:
    type: ProviderEventType
    content: str = ""
    provider_session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class ProviderRunResult:
    content: str
    provider_session_id: str | None = None
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def should_execute_tools(self) -> bool:
        return bool(self.tool_calls) and not self.error


@runtime_checkable
class Provider(Protocol):
    name: str

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        """Start a provider-native conversation and return the final result."""

    def resume_session(
        self,
        provider_session_id: str,
        request: ProviderRequest,
    ) -> ProviderRunResult:
        """Resume a provider-native conversation and return the final result."""

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream normalized provider events for Core consumers."""

    def healthcheck(self) -> ProviderHealth:
        """Return provider readiness without starting a session."""

