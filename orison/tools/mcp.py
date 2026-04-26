"""MCP status/control compatibility tool."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from orison.tools.base import Tool, object_schema


class MCPTool(Tool):
    @property
    def name(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return "Inspect MCP support and configured MCP server declarations from ORISON_MCP_CONFIG."

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema({"action": {"type": "string", "enum": ["status", "list"]}}, required=["action"])

    async def execute(self, action: str = "status", **_: Any) -> str:
        config_path = os.environ.get("ORISON_MCP_CONFIG", "").strip()
        if not config_path:
            return "MCP runtime is available as a schema-compatible control tool; no ORISON_MCP_CONFIG is set."
        try:
            data = json.loads(Path(config_path).expanduser().read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Error reading MCP config: {exc}"
        servers = data.get("mcpServers", data.get("servers", {})) if isinstance(data, dict) else {}
        if action == "list":
            if isinstance(servers, dict) and servers:
                return "Configured MCP servers:\n" + "\n".join(f"- {name}" for name in sorted(servers))
            return "No MCP servers configured."
        return f"MCP config loaded from {config_path}; configured servers: {len(servers) if isinstance(servers, dict) else 0}"
