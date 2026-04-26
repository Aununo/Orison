"""Markdown-backed long-term memory for Orison."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orison.core.storage import CoreStorage


MEMORY_FILES = {
    "user": "USER.md",
    "memory": "MEMORY.md",
    "soul": "SOUL.md",
}


@dataclass(frozen=True)
class MemorySnapshot:
    user: str
    memory: str
    soul: str

    def as_dict(self) -> dict[str, str]:
        return {"user": self.user, "memory": self.memory, "soul": self.soul}


class MarkdownMemoryStore:
    """Stores human-readable memory files and an append audit trail."""

    def __init__(self, memory_dir: str | Path, storage: CoreStorage | None = None) -> None:
        self.memory_dir = Path(memory_dir)
        self.storage = storage
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(
            user=self._read("user"),
            memory=self._read("memory"),
            soul=self._read("soul"),
        )

    def context(self, *, per_file_limit: int = 4000) -> str:
        snapshot = self.snapshot()
        sections = [
            ("USER.md", _memory_body_or_default(snapshot.user, "No user preferences recorded.")[:per_file_limit]),
            ("MEMORY.md", _memory_body_or_default(snapshot.memory, "No long-term facts recorded.")[:per_file_limit]),
            ("SOUL.md", _memory_body_or_default(snapshot.soul, "No agent behavior guidance recorded.")[:per_file_limit]),
        ]
        return "\n\n".join(f"## {name}\n{text.strip()}" for name, text in sections)

    def append(
        self,
        section: str,
        content: str,
        *,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
        created_at: str,
    ) -> str:
        key = self._normalize_section(section)
        clean = _sanitize_memory_text(content)
        if not clean:
            raise ValueError("memory content must be a non-empty string")

        path = self.memory_dir / MEMORY_FILES[key]
        prefix = "\n\n" if path.read_text(encoding="utf-8").strip() else ""
        entry = f"{prefix}- {clean}\n"
        with path.open("a", encoding="utf-8") as file:
            file.write(entry)

        if self.storage is not None:
            self.storage.insert_memory_event(
                section=key,
                content=clean,
                source=source,
                metadata=metadata or {},
                created_at=created_at,
            )
        return clean

    def _read(self, section: str) -> str:
        return (self.memory_dir / MEMORY_FILES[section]).read_text(encoding="utf-8").strip()

    def _ensure_files(self) -> None:
        defaults = {
            "USER.md": "# User Memory\n\n",
            "MEMORY.md": "# Long-Term Memory\n\n",
            "SOUL.md": "# Orison Soul\n\n",
        }
        for filename, text in defaults.items():
            path = self.memory_dir / filename
            if not path.exists():
                path.write_text(text, encoding="utf-8")

    def _normalize_section(self, section: str) -> str:
        key = section.strip().lower()
        if key not in MEMORY_FILES:
            raise ValueError("memory section must be one of: user, memory, soul")
        return key


def _memory_body_or_default(text: str, default: str) -> str:
    meaningful_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        meaningful_lines.append(line)
    body = "\n".join(meaningful_lines).strip()
    return body or default


def _sanitize_memory_text(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(
        r"<recalled_context\b[^>]*>.*?</recalled_context>",
        "",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    stripped = re.sub(
        r"<(?:memory_summary|user_preferences|long_term_facts|agent_guidance)\b[^>]*>.*?</(?:memory_summary|user_preferences|long_term_facts|agent_guidance)>",
        "",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return " ".join(line.strip() for line in stripped.splitlines() if line.strip())
