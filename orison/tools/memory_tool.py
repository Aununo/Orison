"""Long-term memory tool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from orison.memory import MarkdownMemoryStore
from orison.tools.base import Tool, object_schema


class MemoryTool(Tool):
    def __init__(self, memory_store: MarkdownMemoryStore) -> None:
        self.memory_store = memory_store

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Persist or inspect Orison long-term memory. Use append only for stable user preferences, "
            "durable project/environment facts, or agent behavior guidance that should affect future turns."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["append", "snapshot"]},
                "section": {"type": "string", "enum": ["user", "memory", "soul"]},
                "content": {"type": "string"},
            },
            required=["action"],
        )

    async def execute(
        self,
        action: str,
        section: str | None = None,
        content: str | None = None,
        **_: Any,
    ) -> str:
        if action == "snapshot":
            return json.dumps(self.memory_store.snapshot().as_dict(), ensure_ascii=False, sort_keys=True)
        if action == "append":
            if not section:
                return "Error: section is required for append"
            if not content or not content.strip():
                return "Error: content is required for append"
            written = self.memory_store.append(
                section,
                content,
                source="tool",
                metadata={"tool": "memory"},
                created_at=datetime.now(UTC).isoformat(),
            )
            return f"Memory appended to {section}: {written}"
        return f"Error: unknown action {action!r}"
