"""Task spawning and cron scheduling tools."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orison.tools.base import Tool, object_schema
from orison.tools.common import _orison_schedule

if TYPE_CHECKING:
    from orison.cron import CronManager


class SpawnTool(Tool):
    def __init__(self, *, cron: CronManager | None = None, session_id: str) -> None:
        self.cron = cron
        self.session_id = session_id

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a background Orison task. In Core this schedules the task as an immediate "
            "one-shot cron job in an isolated spawn session."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema({"task": {"type": "string"}, "label": {"type": "string"}}, required=["task"])

    async def execute(self, task: str, label: str | None = None, **_: Any) -> str:
        if self.cron is None:
            return "Error: Subagent spawning requires Orison cron runtime"
        job_id = f"spawn-{uuid.uuid4().hex[:12]}"
        job = self.cron.create_job(
            name=label or task[:30],
            prompt=task,
            schedule=f"once:{datetime.now(UTC).isoformat()}",
            target_session=f"{self.session_id}:spawn:{job_id}" if self.session_id else f"spawn:{job_id}",
            enabled=True,
            job_id=job_id,
        )
        return f"Spawned background task '{job.name}' (id: {job.id})"


class CronTool(Tool):
    def __init__(self, cron: CronManager, *, session_id: str) -> None:
        self.cron = cron
        self.session_id = session_id

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Schedule reminders and recurring tasks. Actions: add, list, remove."

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["add", "list", "remove"]},
                "name": {"type": "string"},
                "message": {"type": "string"},
                "every_seconds": {"type": "integer", "minimum": 1},
                "cron_expr": {"type": "string"},
                "tz": {"type": "string", "description": "Accepted for nanobot compatibility; Orison daily cron uses server local timezone"},
                "at": {"type": "string"},
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
