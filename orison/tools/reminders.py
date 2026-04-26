"""QQ reminder tool and direct delivery helpers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from orison.tools.base import Tool, object_schema
from orison.tools.common import _strip_think

if TYPE_CHECKING:
    from orison.cron import CronManager


REMINDER_PROMPT_PREFIX = "orison://reminder/"
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class ReminderTool(Tool):
    def __init__(self, cron: CronManager, *, session_id: str, context_metadata: dict[str, Any] | None = None) -> None:
        self.cron = cron
        self.session_id = session_id
        self.context_metadata = dict(context_metadata or {})

    @property
    def name(self) -> str:
        return "reminder"

    @property
    def description(self) -> str:
        return (
            "Schedule QQ reminders. Use this when the user asks to be reminded at a specific time. "
            "Interpret relative/natural times in Asia/Shanghai unless the user explicitly says another timezone. "
            "For QQ group requests, the reminder will @mention the requesting user in the same group. "
            "Actions: add, list, remove."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "action": {"type": "string", "enum": ["add", "list", "remove"]},
                "name": {"type": "string"},
                "message": {"type": "string", "description": "What to remind the user to do."},
                "remind_at": {
                    "type": "string",
                    "description": "ISO-like date/time for the reminder. If no offset is present, it is Asia/Shanghai time.",
                },
                "job_id": {"type": "string"},
            },
            required=["action"],
        )

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        remind_at: str | None = None,
        job_id: str | None = None,
        **_: Any,
    ) -> str:
        if action == "list":
            jobs = [job for job in self.cron.list_jobs() if job.prompt.startswith(REMINDER_PROMPT_PREFIX)]
            if not jobs:
                return "No reminders."
            return "Reminders:\n" + "\n".join(f"- {job.name} (id: {job.id}, {job.schedule})" for job in jobs)
        if action == "remove":
            if not job_id:
                return "Error: job_id is required for remove"
            return f"Removed reminder {job_id}" if self.cron.delete_job(job_id) else f"Reminder {job_id} not found"
        if action != "add":
            return f"Error: unknown action {action!r}"
        if not message.strip():
            return "Error: message is required when action='add'"
        if not remind_at or not remind_at.strip():
            return "Error: remind_at is required when action='add'"
        try:
            run_at_utc = _parse_shanghai_datetime(remind_at)
        except ValueError as exc:
            return f"Error: {exc}"

        payload = _reminder_payload(
            message=message,
            session_id=self.session_id,
            context_metadata=self.context_metadata,
            created_at=datetime.now(UTC),
        )
        job = self.cron.create_job(
            name=name or f"提醒：{message[:24]}",
            prompt=encode_reminder_prompt(payload),
            schedule=f"once:{run_at_utc.isoformat()}",
            target_session=self.session_id,
            enabled=True,
        )
        return f"Created reminder '{job.name}' (id: {job.id}) for {run_at_utc.astimezone(_SHANGHAI_TZ).isoformat()} Asia/Shanghai"


def encode_reminder_prompt(payload: dict[str, Any]) -> str:
    return REMINDER_PROMPT_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def deliver_reminder_prompt(prompt: str, *, data_dir: Path) -> tuple[str, str | None]:
    if not prompt.startswith(REMINDER_PROMPT_PREFIX):
        return "", "not a reminder prompt"
    try:
        payload = json.loads(prompt.removeprefix(REMINDER_PROMPT_PREFIX))
    except json.JSONDecodeError as exc:
        return "", f"invalid reminder payload: {exc}"
    if not isinstance(payload, dict):
        return "", "invalid reminder payload"

    outbox_file = _write_reminder_outbox(payload, data_dir=data_dir)
    if payload.get("channel") != "qq":
        return f"Queued reminder at {outbox_file}", None

    url = os.environ.get("ORISON_ONEBOT_HTTP_URL", "http://127.0.0.1:15150").rstrip("/")
    action, params = _onebot_reminder_request(payload)
    if not action:
        return f"Queued reminder at {outbox_file}", "reminder has no valid QQ target"
    try:
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/{action}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            response_text = resp.read().decode("utf-8", "replace")
        return f"Sent QQ reminder via {action}; queued copy at {outbox_file}; response: {response_text}", None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"Queued reminder at {outbox_file}", f"failed to send QQ reminder: {exc}"


def _parse_shanghai_datetime(value: str) -> datetime:
    clean = value.strip().replace(" ", "T", 1)
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("remind_at must be an ISO date/time, e.g. 2026-04-27 09:30") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
    return parsed.astimezone(UTC)


def _reminder_payload(
    *,
    message: str,
    session_id: str,
    context_metadata: dict[str, Any],
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "kind": "qq_reminder",
        "channel": str(context_metadata.get("channel") or ""),
        "message_type": str(context_metadata.get("message_type") or ""),
        "group_id": str(context_metadata.get("group_id") or ""),
        "user_id": str(context_metadata.get("user_id") or ""),
        "message": _strip_think(message),
        "session_id": session_id,
        "created_at": created_at.astimezone(UTC).isoformat(),
    }


def _write_reminder_outbox(payload: dict[str, Any], *, data_dir: Path) -> Path:
    outbox = data_dir / "reminders_outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    outbox_file = outbox / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}.json"
    outbox_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return outbox_file


def _onebot_reminder_request(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    message = str(payload.get("message") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    message_type = str(payload.get("message_type") or "").strip()
    text = f" 提醒：{message}" if message else " 提醒时间到了"
    if message_type == "group" and group_id and user_id:
        return "send_group_msg", {
            "group_id": group_id,
            "message": [
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": text}},
            ],
        }
    if user_id:
        return "send_private_msg", {"user_id": user_id, "message": text.strip()}
    return "", {}
