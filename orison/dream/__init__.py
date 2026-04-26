"""Controlled reflection from Orison history into memory and skills."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orison.core.storage import CoreStorage
from orison.memory import MarkdownMemoryStore
from orison.providers import ProviderMessage, ProviderRegistry, ProviderRequest
from orison.skills import SkillRegistry


@dataclass(frozen=True)
class DreamResult:
    run_id: str
    provider_name: str
    status: str
    cursor_before: str | None
    cursor_after: str | None
    processed_run_count: int
    summary: str
    changelog_path: str
    memory_updates: dict[str, list[str]]
    skills_written: list[str]
    rejected_changes: list[str]
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class DreamManager:
    """Runs an auditable, write-scoped reflection pass."""

    def __init__(
        self,
        *,
        storage: CoreStorage,
        registry: ProviderRegistry,
        memory_store: MarkdownMemoryStore,
        skill_registry: SkillRegistry,
        data_dir: str | Path,
    ) -> None:
        self.storage = storage
        self.registry = registry
        self.memory_store = memory_store
        self.skill_registry = skill_registry
        self.data_dir = Path(data_dir)
        self.changelog_dir = self.data_dir / "dream" / "changelog"

    def run(self, *, provider_name: str | None = None, limit: int = 50) -> DreamResult:
        started_at = _now()
        run_id = f"dream-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        selected_provider = provider_name or self.registry.default_name
        provider = self.registry.get(selected_provider)
        cursor_before = self.storage.latest_dream_cursor()
        recent_runs = self.storage.list_recent_run_records(after_created_at=cursor_before, limit=limit)
        cursor_after = str(recent_runs[-1]["created_at"]) if recent_runs else cursor_before

        if not recent_runs:
            result = DreamResult(
                run_id=run_id,
                provider_name=selected_provider,
                status="success",
                cursor_before=cursor_before,
                cursor_after=cursor_after,
                processed_run_count=0,
                summary="No new history to process.",
                changelog_path="",
                memory_updates={"user": [], "memory": [], "soul": []},
                skills_written=[],
                rejected_changes=[],
            )
            return self._finish_result(result, raw_response="", started_at=started_at)

        request = ProviderRequest(
            messages=[
                ProviderMessage(
                    role="user",
                    content=json.dumps(
                        self._dream_input(recent_runs),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            ],
            system_context=_DREAM_SYSTEM_PROMPT,
            session_id=run_id,
            metadata={"orison_internal": "dream", "dream_run_id": run_id},
        )
        provider_result = provider.start_session(request)
        if provider_result.error:
            result = DreamResult(
                run_id=run_id,
                provider_name=selected_provider,
                status="error",
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                processed_run_count=len(recent_runs),
                summary="Dream provider failed.",
                changelog_path="",
                memory_updates={"user": [], "memory": [], "soul": []},
                skills_written=[],
                rejected_changes=[],
                error=provider_result.error,
            )
            return self._finish_result(result, raw_response=provider_result.content, started_at=started_at)

        try:
            proposal = _parse_provider_json(provider_result.content)
            applied = self._apply_proposal(proposal, run_id=run_id)
            status = "success"
            error = None
            summary = str(proposal.get("summary") or "Dream completed.")
        except ValueError as exc:
            applied = _empty_applied()
            status = "error"
            error = str(exc)
            summary = "Dream response could not be applied."
            cursor_after = cursor_before

        result = DreamResult(
            run_id=run_id,
            provider_name=selected_provider,
            status=status,
            cursor_before=cursor_before,
            cursor_after=cursor_after,
            processed_run_count=len(recent_runs),
            summary=summary,
            changelog_path="",
            memory_updates=applied["memory_updates"],
            skills_written=applied["skills_written"],
            rejected_changes=applied["rejected_changes"],
            error=error,
        )
        return self._finish_result(result, raw_response=provider_result.content, started_at=started_at)

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.storage.list_dream_runs(limit=limit)

    def _dream_input(self, recent_runs: list[dict[str, Any]]) -> dict[str, Any]:
        failures = [run for run in recent_runs if run.get("error")]
        return {
            "unprocessed_runs": recent_runs,
            "failures": failures,
            "memory": self.memory_store.snapshot().as_dict(),
            "skills": [skill.summary_line() for skill in self.skill_registry.list_skills()],
            "allowed_writes": [
                "memory/USER.md",
                "memory/MEMORY.md",
                "memory/SOUL.md",
                "skills/*/SKILL.md",
            ],
            "forbidden_writes": ["orison/core", "orison/providers", "channels", "cmd", "config.toml"],
        }

    def _apply_proposal(self, proposal: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        applied = _empty_applied()
        explicit_rejections = proposal.get("rejected_changes", [])
        if isinstance(explicit_rejections, list):
            applied["rejected_changes"].extend(str(item) for item in explicit_rejections if str(item).strip())

        if "files" in proposal or "file_changes" in proposal or "changes" in proposal:
            applied["rejected_changes"].append("Ignored unsupported file change proposal outside memory/skills schema.")

        memory = proposal.get("memory", {})
        if memory is None:
            memory = {}
        if not isinstance(memory, dict):
            raise ValueError("dream memory proposal must be an object")
        for section, entries in memory.items():
            section_name = str(section).strip().lower()
            if section_name not in {"user", "memory", "soul"}:
                applied["rejected_changes"].append(f"Rejected unsupported memory section: {section}")
                continue
            if isinstance(entries, str):
                entry_list = [entries]
            elif isinstance(entries, list):
                entry_list = [str(entry) for entry in entries]
            else:
                applied["rejected_changes"].append(f"Rejected non-list memory update for section: {section}")
                continue
            for entry in entry_list:
                clean = entry.strip()
                if not clean:
                    continue
                written = self.memory_store.append(
                    section_name,
                    clean,
                    source="dream",
                    metadata={"dream_run_id": run_id},
                    created_at=_iso(_now()),
                )
                applied["memory_updates"][section_name].append(written)

        skills = proposal.get("skills", proposal.get("skill_updates", []))
        if isinstance(skills, dict):
            skills = [skills]
        if not isinstance(skills, list):
            raise ValueError("dream skills proposal must be a list")
        for skill in skills:
            if not isinstance(skill, dict):
                applied["rejected_changes"].append("Rejected non-object skill proposal.")
                continue
            try:
                name = _clean_skill_name(str(skill.get("name") or ""))
                description = _clean_description(str(skill.get("description") or ""))
                body = _clean_text(str(skill.get("body") or skill.get("content") or ""), "skill body")
            except ValueError as exc:
                applied["rejected_changes"].append(str(exc))
                continue
            self._write_skill(name=name, description=description, body=body)
            applied["skills_written"].append(name)

        return applied

    def _write_skill(self, *, name: str, description: str, body: str) -> None:
        skill_dir = self.skill_registry.skills_dir / name
        skill_path = skill_dir / "SKILL.md"
        skill_dir.mkdir(parents=True, exist_ok=True)
        clean_body = _strip_frontmatter(body).strip()
        skill_path.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n{clean_body}\n",
            encoding="utf-8",
        )

    def _finish_result(self, result: DreamResult, *, raw_response: str, started_at: datetime) -> DreamResult:
        changelog_path = self._write_changelog(result, raw_response=raw_response)
        completed_at = _iso(_now())
        final = DreamResult(
            **{
                **asdict(result),
                "changelog_path": str(changelog_path),
            }
        )
        self.storage.insert_dream_run(
            run_id=final.run_id,
            provider_name=final.provider_name,
            status=final.status,
            cursor_before=final.cursor_before,
            cursor_after=final.cursor_after,
            processed_run_count=final.processed_run_count,
            summary=final.summary,
            changelog_path=final.changelog_path,
            rejected_changes=final.rejected_changes,
            error=final.error,
            raw_response=raw_response,
            started_at=_iso(started_at),
            completed_at=completed_at,
        )
        return final

    def _write_changelog(self, result: DreamResult, *, raw_response: str) -> Path:
        self.changelog_dir.mkdir(parents=True, exist_ok=True)
        path = self.changelog_dir / f"{result.run_id}.md"
        memory_lines = [
            f"- {section}: {entry}"
            for section, entries in result.memory_updates.items()
            for entry in entries
        ]
        skill_lines = [f"- {name}" for name in result.skills_written]
        rejected_lines = [f"- {item}" for item in result.rejected_changes]
        path.write_text(
            "\n".join(
                [
                    f"# Dream Changelog {result.run_id}",
                    "",
                    f"- Status: {result.status}",
                    f"- Provider: {result.provider_name}",
                    f"- Cursor before: {result.cursor_before or ''}",
                    f"- Cursor after: {result.cursor_after or ''}",
                    f"- Processed runs: {result.processed_run_count}",
                    f"- Summary: {result.summary}",
                    f"- Error: {result.error or ''}",
                    "",
                    "## Memory Updates",
                    *(memory_lines or ["- None"]),
                    "",
                    "## Skills Written",
                    *(skill_lines or ["- None"]),
                    "",
                    "## Rejected Changes",
                    *(rejected_lines or ["- None"]),
                    "",
                    "## Raw Provider Response",
                    "```json",
                    raw_response.strip(),
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path


def _empty_applied() -> dict[str, Any]:
    return {
        "memory_updates": {"user": [], "memory": [], "soul": []},
        "skills_written": [],
        "rejected_changes": [],
    }


def _parse_provider_json(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("dream provider response must contain a JSON object")
        payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("dream provider response must be a JSON object")
    return payload


def _clean_skill_name(name: str) -> str:
    clean = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", clean):
        raise ValueError(f"Rejected invalid skill name: {name}")
    return clean


def _clean_text(value: str, label: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"Rejected empty {label}.")
    return clean


def _clean_description(value: str) -> str:
    clean = _clean_text(value, "skill description")
    return " ".join(line.strip() for line in clean.splitlines() if line.strip())


def _strip_frontmatter(body: str) -> str:
    text = body.strip()
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    return text[end + 4 :].lstrip()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


_DREAM_SYSTEM_PROMPT = """You are Orison Dream, a controlled reflection loop.

Read the supplied JSON context and extract only stable, reusable learnings.
Return strict JSON only in this shape:
{
  "summary": "short audit summary",
  "memory": {
    "user": ["stable user preferences"],
    "memory": ["stable project facts or durable lessons"],
    "soul": ["agent behavior guidance"]
  },
  "skills": [
    {
      "name": "simple-skill-name",
      "description": "one sentence",
      "body": "# Skill\nReusable procedure."
    }
  ],
  "rejected_changes": ["anything considered but not applied"]
}

Allowed writes are limited to memory sections and skills/*/SKILL.md. Do not
propose edits to Core, Providers, Channels, command entry points, config, or
startup scripts. If evidence is weak, prefer returning no update.
"""
