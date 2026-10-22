"""Long-term memory tool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from orison.memory import MarkdownMemoryStore
from orison.tools.base import Tool, object_schema


class MemoryTool(Tool):
    def __init__(self, memory_store: MarkdownMemoryStore, context_metadata: dict[str, Any] | None = None) -> None:
        self.memory_store = memory_store
        self.context = context_metadata or {}
        self.scope = str(self.context.get("session_id") or "global")

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Recall, correct, or explicitly forget long-term memories. Search with English keywords, "
            "including when the conversation uses another language. Write memory content in English. "
            "Save stable preferences, meaningful shared experiences, and commitments with the correct "
            "person and conversation. Exclude secrets, transient noise, and unsupported claims. "
            "Use the same memory_key and subject to replace a corrected fact. Pin only on an explicit "
            "request to remember permanently. Core identity cannot be edited through this tool."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["search", "remember", "forget"]},
                "section": {"type": "string", "enum": ["user", "memory"]},
                "content": {"type": "string"},
                "query": {"type": "string"},
                "memory_id": {"type": "string"},
                "memory_key": {"type": "string"},
                "subject": {"type": "string", "description": "The actual subject's stable user ID, not necessarily the current speaker."},
                "kind": {"type": "string", "enum": ["ordinary", "preference", "episode", "commitment"]},
                "pinned": {"type": "boolean"},
                "expires_at": {"type": "string", "description": "Optional ISO timestamp with an explicit timezone."},
            },
            required=["action"],
        )

    async def execute(
        self,
        action: str,
        section: str | None = None,
        content: str | None = None,
        query: str = "",
        memory_id: str = "",
        memory_key: str = "",
        subject: str | None = None,
        kind: str = "preference",
        pinned: bool = False,
        expires_at: str | None = None,
        **_: Any,
    ) -> str:
        if action == "search":
            return json.dumps(self.memory_store.search(query, scope=self.scope, subject=subject or ""), ensure_ascii=True)
        if action == "forget":
            if not memory_id:
                return "Error: memory_id is required for forget"
            include_global = self.context.get("role") in {"father", "mother"} or self.context.get("channel") == "local"
            return "Memory forgotten." if self.memory_store.forget(memory_id, scope=self.scope, include_global=include_global) else "Error: memory not found in this conversation"
        if action == "remember":
            if not content:
                return "Error: content is required for remember"
            identifier = self.memory_store.remember(
                content, scope=self.scope, subject=subject if subject is not None else str(self.context.get("user_id") or ""),
                section=section or "memory", kind=kind, memory_key=memory_key, pinned=pinned, expires_at=expires_at,
                sources=[{"run_id": self.context.get("run_id", ""), "excerpt": str(self.context.get("user_message", ""))[:256]}],
                confirmed_at=str(self.context.get("timestamp") or datetime.now(UTC).isoformat()),
            )
            return f"Memory saved (id: {identifier})."
        return f"Error: unknown action {action!r}"
