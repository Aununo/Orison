"""Scheduled Core jobs for Orison."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Callable

from orison.core.storage import CoreStorage
from orison.timezone import BOT_TIMEZONE


DEFAULT_DREAM_SCHEDULE = "daily:03:00"


@dataclass(frozen=True)
class CronJob:
    id: str
    name: str
    prompt: str
    schedule: str
    provider: str | None
    target_session: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    last_status: str
    last_error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CronRunResult:
    job: CronJob
    run_id: str | None
    response: str
    error: str | None


RunJobCallback = Callable[[CronJob], CronRunResult]


class CronManager:
    """Owns durable cron jobs and invokes the Core session flow when due."""

    def __init__(self, storage: CoreStorage, run_job: RunJobCallback) -> None:
        self.storage = storage
        self._run_job = run_job
        # Repair daily timestamps persisted when schedules used the host timezone.
        for job in self.list_jobs():
            if not job.enabled or not job.schedule.startswith("daily:"):
                continue
            scheduled_time = (
                _parse_iso(job.next_run_at).astimezone(BOT_TIMEZONE).time() if job.next_run_at else None
            )
            if scheduled_time != _parse_daily_time(job.schedule.removeprefix("daily:")):
                now = _utc_now()
                updated = _replace_job(job, next_run_at=_next_run_at(job.schedule, now), updated_at=_iso(now))
                self.storage.upsert_cron_job(asdict(updated))

    def list_jobs(self) -> list[CronJob]:
        return [_job_from_dict(raw) for raw in self.storage.list_cron_jobs()]

    def get_job(self, job_id: str) -> CronJob | None:
        raw = self.storage.get_cron_job(job_id)
        return _job_from_dict(raw) if raw else None

    def create_job(
        self,
        *,
        name: str,
        prompt: str,
        schedule: str,
        provider: str | None = None,
        target_session: str | None = None,
        enabled: bool = True,
        job_id: str | None = None,
    ) -> CronJob:
        clean_name = _non_empty(name, "name")
        clean_prompt = _non_empty(prompt, "prompt")
        clean_schedule = _non_empty(schedule, "schedule")
        _next_run_at(clean_schedule, _utc_now())

        now = _utc_now()
        job_id = _clean_job_id(job_id) if job_id else f"cron-{uuid.uuid4().hex[:12]}"
        job = CronJob(
            id=job_id,
            name=clean_name,
            prompt=clean_prompt,
            schedule=clean_schedule,
            provider=_clean_optional(provider),
            target_session=_clean_optional(target_session) or f"cron:{job_id}",
            enabled=enabled,
            last_run_at=None,
            next_run_at=_next_run_at(clean_schedule, now) if enabled else None,
            last_status="pending",
            last_error=None,
            created_at=_iso(now),
            updated_at=_iso(now),
        )
        self.storage.upsert_cron_job(asdict(job))
        return job

    def pause_job(self, job_id: str) -> CronJob:
        job = self._require_job(job_id)
        updated = _replace_job(
            job,
            enabled=False,
            next_run_at=None,
            last_status="paused",
            updated_at=_iso(_utc_now()),
        )
        self.storage.upsert_cron_job(asdict(updated))
        return updated

    def resume_job(self, job_id: str) -> CronJob:
        job = self._require_job(job_id)
        now = _utc_now()
        updated = _replace_job(
            job,
            enabled=True,
            next_run_at=_next_run_at(job.schedule, now),
            last_status="pending",
            last_error=None,
            updated_at=_iso(now),
        )
        self.storage.upsert_cron_job(asdict(updated))
        return updated

    def delete_job(self, job_id: str) -> bool:
        return self.storage.delete_cron_job(job_id)

    def reschedule_job(self, job_id: str, schedule: str) -> CronJob:
        job = self._require_job(job_id)
        schedule = _non_empty(schedule, "schedule")
        now = _utc_now()
        next_run = _next_run_at(schedule, now)
        updated = _replace_job(
            job, schedule=schedule, next_run_at=next_run if job.enabled else None, updated_at=_iso(now),
        )
        self.storage.upsert_cron_job(asdict(updated))
        return updated

    def trigger_now(self, job_id: str) -> CronRunResult:
        return self._run_and_update(self._require_job(job_id), manual=True)

    def run_due_once(self) -> list[CronRunResult]:
        now = _iso(_utc_now())
        results: list[CronRunResult] = []
        for raw in self.storage.due_cron_jobs(now):
            results.append(self._run_and_update(_job_from_dict(raw), manual=False))
        return results

    def _run_and_update(self, job: CronJob, *, manual: bool) -> CronRunResult:
        started_at = _utc_now()
        try:
            result = self._run_job(job)
        except Exception as exc:
            result = CronRunResult(job=job, run_id=None, response="", error=str(exc))

        status = "error" if result.error else "success"
        next_run = job.next_run_at if manual else None
        if job.enabled and not manual:
            next_run = _next_run_at(job.schedule, started_at, after_run=True)
        if job.schedule.startswith("once:") and not manual:
            next_run = None

        updated = _replace_job(
            job,
            enabled=job.enabled and not (job.schedule.startswith("once:") and not manual),
            last_run_at=_iso(started_at),
            next_run_at=next_run,
            last_status=status,
            last_error=result.error,
            updated_at=_iso(_utc_now()),
        )
        self.storage.upsert_cron_job(asdict(updated))
        return CronRunResult(
            job=updated,
            run_id=result.run_id,
            response=result.response,
            error=result.error,
        )

    def _require_job(self, job_id: str) -> CronJob:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError("cron job not found")
        return job


def cron_job_payload(job: CronJob) -> dict[str, object]:
    return asdict(job)


def cron_run_payload(result: CronRunResult) -> dict[str, object]:
    return {
        "job": cron_job_payload(result.job),
        "run_id": result.run_id,
        "response": result.response,
        "error": result.error,
    }


def _next_run_at(schedule: str, now: datetime, *, after_run: bool = False) -> str | None:
    """Return a UTC timestamp, interpreting naive and daily schedules in Singapore time."""
    if schedule.startswith("once:"):
        run_at = _parse_iso(schedule.removeprefix("once:"))
        if after_run:
            return None
        return _iso(run_at)

    if schedule.startswith("interval:"):
        interval = _parse_interval(schedule.removeprefix("interval:"))
        return _iso(now + interval)

    if schedule.startswith("daily:"):
        daily_time = _parse_daily_time(schedule.removeprefix("daily:"))
        local_now = now.astimezone(BOT_TIMEZONE)
        candidate = datetime.combine(local_now.date(), daily_time, tzinfo=BOT_TIMEZONE)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return _iso(candidate)

    raise ValueError("schedule must start with once:, interval:, or daily:")


def _parse_iso(value: str) -> datetime:
    clean = value.strip()
    if not clean:
        raise ValueError("once schedule requires an ISO timestamp")
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("once schedule requires an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BOT_TIMEZONE)
    return parsed.astimezone(UTC)


def _parse_interval(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([smhd])", value.strip())
    if not match:
        raise ValueError("interval schedule must look like interval:30m")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("interval amount must be positive")
    unit = match.group(2)
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _parse_daily_time(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("daily schedule must look like daily:09:00")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("daily schedule must look like daily:09:00") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("daily schedule time is out of range")
    return time(hour=hour, minute=minute)


def _replace_job(job: CronJob, **updates: object) -> CronJob:
    values = asdict(job)
    values.update(updates)
    return CronJob(**values)


def _job_from_dict(raw: dict[str, object]) -> CronJob:
    return CronJob(
        id=str(raw["id"]),
        name=str(raw["name"]),
        prompt=str(raw["prompt"]),
        schedule=str(raw["schedule"]),
        provider=str(raw["provider"]) if raw.get("provider") else None,
        target_session=str(raw["target_session"]),
        enabled=bool(raw["enabled"]),
        last_run_at=str(raw["last_run_at"]) if raw.get("last_run_at") else None,
        next_run_at=str(raw["next_run_at"]) if raw.get("next_run_at") else None,
        last_status=str(raw["last_status"]),
        last_error=str(raw["last_error"]) if raw.get("last_error") else None,
        created_at=str(raw["created_at"]),
        updated_at=str(raw["updated_at"]),
    )


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string value must be a string")
    clean = value.strip()
    return clean or None


def _clean_job_id(value: str) -> str:
    clean = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", clean):
        raise ValueError("cron job id must contain only letters, numbers, colon, underscore, or dash")
    return clean


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
