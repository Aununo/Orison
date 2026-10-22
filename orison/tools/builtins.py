"""Built-in tool registry assembly for Orison Core."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orison.tools.base import ToolRegistry
from orison.tools.memory_tool import MemoryTool
from orison.tools.messaging import MessageTool
from orison.tools.reminders import ReminderTool
from orison.tools.tasks import CronTool
from orison.tools.todos import TodoTool
from orison.tools.web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from orison.core.storage import CoreStorage
    from orison.cron import CronManager
    from orison.memory import MarkdownMemoryStore


def build_default_tool_registry(
    *,
    data_dir: str | Path,
    storage: CoreStorage | None = None,
    cron: CronManager | None = None,
    memory_store: MarkdownMemoryStore | None = None,
    session_id: str = "",
    context_metadata: dict[str, object] | None = None,
    family: dict[str, object] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())
    if memory_store is not None:
        registry.register(MemoryTool(memory_store, context_metadata))
    registry.register(MessageTool(data_dir=Path(data_dir), session_id=session_id, context_metadata=context_metadata, family=family))
    if storage is not None:
        registry.register(TodoTool(storage=storage, session_id=session_id, context_metadata=context_metadata))
    if cron is not None:
        registry.register(CronTool(cron, session_id=session_id))
        registry.register(ReminderTool(cron, session_id=session_id, context_metadata=context_metadata))
    return registry
