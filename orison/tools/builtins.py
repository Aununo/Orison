"""Built-in tool registry assembly for Orison Core."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orison.tools.base import ToolRegistry
from orison.tools.filesystem import EditFileTool, ListDirTool, NotebookEditTool, ReadFileTool, WriteFileTool
from orison.tools.mcp import MCPTool
from orison.tools.memory_tool import MemoryTool
from orison.tools.messaging import MessageTool
from orison.tools.reminders import ReminderTool
from orison.tools.search import GlobTool, GrepTool
from orison.tools.shell import ExecTool
from orison.tools.state import MyTool
from orison.tools.tasks import CronTool, SpawnTool
from orison.tools.todos import TodoTool
from orison.tools.web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from orison.core.storage import CoreStorage
    from orison.cron import CronManager
    from orison.memory import MarkdownMemoryStore


def build_default_tool_registry(
    *,
    workspace: str | Path,
    data_dir: str | Path,
    storage: CoreStorage | None = None,
    cron: CronManager | None = None,
    memory_store: MarkdownMemoryStore | None = None,
    session_id: str = "",
    context_metadata: dict[str, object] | None = None,
) -> ToolRegistry:
    root = Path(workspace).expanduser().resolve()
    registry = ToolRegistry()
    registry.register(MyTool(registry, workspace=root))
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(ListDirTool(root))
    registry.register(GlobTool(root))
    registry.register(GrepTool(root))
    registry.register(NotebookEditTool(root))
    registry.register(ExecTool(root))
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())
    if memory_store is not None:
        registry.register(MemoryTool(memory_store))
    registry.register(MessageTool(data_dir=Path(data_dir), session_id=session_id))
    registry.register(SpawnTool(cron=cron, session_id=session_id))
    if storage is not None:
        registry.register(TodoTool(storage=storage, session_id=session_id, context_metadata=context_metadata))
    if cron is not None:
        registry.register(CronTool(cron, session_id=session_id))
        registry.register(ReminderTool(cron, session_id=session_id, context_metadata=context_metadata))
    registry.register(MCPTool())
    return registry
