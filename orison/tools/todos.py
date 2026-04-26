"""Persistent todo tool."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from orison.tools.base import Tool, object_schema
from orison.tools.common import _strip_think

if TYPE_CHECKING:
    from orison.core.storage import CoreStorage

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class TodoTool(Tool):
    def __init__(self, *, storage: CoreStorage, session_id: str, context_metadata: dict[str, Any] | None = None) -> None:
        self.storage = storage
        self.session_id = session_id
        self.context_metadata = dict(context_metadata or {})

    @property
    def name(self) -> str:
        return "todo"

    @property
    def description(self) -> str:
        return (
            "Manage persistent user todo items. Use this for requests to add, list, complete, update, or remove todos. "
            "Do not use memory for transient tasks. Use reminder only when the user explicitly asks to be reminded at a time. "
            "Todos are scoped to the current channel/user/group. Actions: add, list, done, update, remove."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["add", "list", "done", "update", "remove"]},
                "id": {"type": "string", "description": "Todo id for done/update/remove."},
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "due_at": {"type": "string", "description": "Optional due date/time, preferably Asia/Shanghai if naive."},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                "status": {"type": "string", "enum": ["open", "done", "cancelled", "all"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            required=["action"],
        )

    async def execute(
        self,
        action: str,
        id: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        due_at: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        limit: int = 50,
        **_: Any,
    ) -> str:
        scope = self._scope()
        action = action.strip().lower()
        if action == "add":
            clean_title = (title or "").strip()
            if not clean_title:
                return "Error: title is required when action='add'"
            clean_priority = _normalize_todo_priority(priority)
            if clean_priority.startswith("Error:"):
                return clean_priority
            try:
                clean_due_at = _normalize_todo_due_at(due_at)
            except ValueError as exc:
                return f"Error: {exc}"
            now = datetime.now(UTC).isoformat()
            todo_id = f"todo-{uuid.uuid4().hex[:12]}"
            self.storage.create_todo(
                {
                    "id": todo_id,
                    "session_id": self.session_id,
                    **scope,
                    "title": clean_title,
                    "notes": _strip_think(notes or "").strip(),
                    "status": "open",
                    "priority": clean_priority,
                    "due_at": clean_due_at,
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": None,
                }
            )
            return f"Created todo {todo_id}: {clean_title}"

        if action == "list":
            clean_status = (status or "open").strip().lower()
            status_filter = None if clean_status == "all" else clean_status
            if status_filter not in {"open", "done", "cancelled", None}:
                return "Error: status must be one of open, done, cancelled, all"
            todos = self.storage.list_todos(**scope, status=status_filter, limit=limit)
            if not todos:
                label = "todos" if status_filter is None else f"{status_filter} todos"
                return f"No {label}."
            return _format_todos(todos)

        if action in {"done", "remove", "update"}:
            clean_id = (id or "").strip()
            if not clean_id:
                return f"Error: id is required for {action}"
            existing = self.storage.get_todo(clean_id, **scope)
            if existing is None:
                return f"Todo {clean_id} not found"
            if action == "remove":
                self.storage.delete_todo(clean_id, **scope)
                return f"Removed todo {clean_id}"
            now = datetime.now(UTC).isoformat()
            fields: dict[str, Any] = {"updated_at": now}
            if action == "done":
                fields.update({"status": "done", "completed_at": now})
            else:
                if title is not None:
                    clean_title = title.strip()
                    if not clean_title:
                        return "Error: title cannot be empty"
                    fields["title"] = clean_title
                if notes is not None:
                    fields["notes"] = _strip_think(notes).strip()
                if due_at is not None:
                    try:
                        fields["due_at"] = _normalize_todo_due_at(due_at)
                    except ValueError as exc:
                        return f"Error: {exc}"
                if priority is not None:
                    clean_priority = _normalize_todo_priority(priority)
                    if clean_priority.startswith("Error:"):
                        return clean_priority
                    fields["priority"] = clean_priority
                if status is not None:
                    clean_status = status.strip().lower()
                    if clean_status == "all" or clean_status not in {"open", "done", "cancelled"}:
                        return "Error: status must be one of open, done, cancelled"
                    fields["status"] = clean_status
                    fields["completed_at"] = now if clean_status == "done" else None
            updated = self.storage.update_todo(clean_id, **scope, fields=fields)
            if updated is None:
                return f"Todo {clean_id} not found"
            verb = "Completed" if action == "done" else "Updated"
            return f"{verb} todo {clean_id}: {updated['title']}"

        return f"Error: unknown action {action!r}"

    def _scope(self) -> dict[str, str]:
        group_id = str(self.context_metadata.get("group_id") or "").strip()
        return {
            "channel": str(self.context_metadata.get("channel") or "").strip(),
            "user_id": str(self.context_metadata.get("user_id") or "").strip(),
            "group_id": group_id,
        }


def _normalize_todo_priority(value: str | None) -> str:
    priority = (value or "normal").strip().lower()
    if priority not in {"low", "normal", "high"}:
        return "Error: priority must be one of low, normal, high"
    return priority


def _normalize_todo_due_at(value: str | None) -> str | None:
    clean = (value or "").strip()
    if not clean:
        return None
    iso_like = clean.replace(" ", "T", 1)
    if iso_like.endswith("Z"):
        iso_like = iso_like[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(iso_like)
    except ValueError as exc:
        raise ValueError("due_at must be an ISO date/time, e.g. 2026-04-27 21:00") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
    return parsed.isoformat()


def _format_todos(todos: list[dict[str, Any]]) -> str:
    lines = ["Todos:"]
    for item in todos:
        due = f", due {item['due_at']}" if item.get("due_at") else ""
        notes = f" — {item['notes']}" if item.get("notes") else ""
        lines.append(f"- {item['id']} [{item['status']}/{item['priority']}{due}] {item['title']}{notes}")
    return "\n".join(lines)
