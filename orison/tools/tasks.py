"""Cron scheduling tool for reminders and recurring activities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orison.tools.base import Tool, object_schema
from orison.tools.common import _orison_schedule
from orison.timezone import BOT_TIMEZONE

if TYPE_CHECKING:
    from orison.cron import CronManager


class CronTool(Tool):
    def __init__(self, cron: CronManager, *, session_id: str) -> None:
        self.cron = cron
        self.session_id = session_id

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            f"Schedule reminders and recurring tasks in {BOT_TIMEZONE.key}. Actions: add, list, remove. "
            "For proactive reminders, the scheduled message must instruct the message tool to notify this "
            "conversation and include the requesting person's QQ ID when a mention is needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["add", "list", "remove"]},
                "name": {"type": "string"},
                "message": {"type": "string"},
                "every_seconds": {"type": "integer", "minimum": 1},
                "cron_expr": {"type": "string"},
                "tz": {"type": "string", "description": f"Accepted for nanobot compatibility; Orison daily cron always uses {BOT_TIMEZONE.key}"},
                "at": {"type": "string", "description": f"ISO date/time; naive values use {BOT_TIMEZONE.key}."},
                "deliver": {"type": "boolean", "description": "Accepted for nanobot compatibility; Orison stores results in session history"},
                "job_id": {"type": "string"},
            },
            required=["action"],
        )

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        deliver: bool = True,
        **_: Any,
    ) -> str:
        if action == "list":
            jobs = self.cron.list_jobs()
            if not jobs:
                return "No scheduled jobs."
            return "Scheduled jobs:\n" + "\n".join(f"- {job.name} (id: {job.id}, {job.schedule})" for job in jobs)
        if action == "remove":
            if not job_id:
                return "Error: job_id is required for remove"
            return f"Removed job {job_id}" if self.cron.delete_job(job_id) else f"Job {job_id} not found"
        if action != "add":
            return f"Error: unknown action {action!r}"
        if not message.strip():
            return "Error: message is required when action='add'"
        schedule = _orison_schedule(every_seconds=every_seconds, cron_expr=cron_expr, at=at, tz=tz)
        if schedule.startswith("Error:"):
            return schedule
        job = self.cron.create_job(
            name=name or message[:30],
            prompt=message,
            schedule=schedule,
            target_session=self.session_id,
            enabled=True,
        )
        return f"Created job '{job.name}' (id: {job.id})"
