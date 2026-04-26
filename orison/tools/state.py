"""Runtime state/scratchpad tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orison.tools.base import Tool, ToolRegistry, object_schema


class MyTool(Tool):
    def __init__(self, registry: ToolRegistry, *, workspace: Path) -> None:
        self.registry = registry
        self.workspace = workspace
        self.scratchpad: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "my"

    @property
    def description(self) -> str:
        return "Check Orison runtime state and maintain a per-process scratchpad. Actions: check, set."

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["check", "set"]},
                "key": {"type": "string", "description": "Optional key, such as tools, workspace, or scratchpad.foo"},
                "value": {"description": "Value for action='set' scratchpad keys"},
            },
            required=["action"],
        )

    async def execute(self, action: str, key: str | None = None, value: Any = None, **_: Any) -> str:
        if action == "check":
            if key == "tools":
                return f"tools: {', '.join(self.registry.tool_names)}"
            if key == "workspace":
                return f"workspace: {self.workspace}"
            if key and key.startswith("scratchpad."):
                item = key.split(".", 1)[1]
                return f"{key}: {self.scratchpad.get(item)!r}"
            return (
                f"workspace: {self.workspace}\n"
                f"tools: {', '.join(self.registry.tool_names)}\n"
                f"scratchpad_keys: {', '.join(sorted(self.scratchpad)) or '(empty)'}"
            )
        if action == "set":
            if not key or not key.startswith("scratchpad."):
                return "Error: only scratchpad.<key> can be set"
            item = key.split(".", 1)[1].strip()
            if not item:
                return "Error: scratchpad key cannot be empty"
            self.scratchpad[item] = value
            return f"Set scratchpad.{item}"
        return f"Error: unknown action {action!r}"
