"""Private persona files and bounded, structured long-term memory."""

from __future__ import annotations

import re
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orison import limits

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
    """Preserves the owner persona and maintains scoped SQLite memories."""

    def __init__(self, memory_dir: str | Path, storage: CoreStorage | None = None) -> None:
        self.memory_dir = Path(memory_dir)
        self.storage = storage
        self.memory_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ensure_files()
        if self.storage is not None:
            self._import_legacy()

    def snapshot(self, *, scope: str | None = None) -> MemorySnapshot:
        if self.storage is None:
            return MemorySnapshot(self._read("user"), self._read("memory"), self._read("soul"))
        items = self.search("", scope=scope, limit=50)
        return MemorySnapshot(
            user="# User Memory\n\n" + "\n".join(f"- {m['content']}" for m in items if m["section"] == "user"),
            memory="# Long-Term Memory\n\n" + "\n".join(f"- {m['content']}" for m in items if m["section"] == "memory"),
            soul=self._read("soul"),
        )

    def context(self, *, per_file_limit: int = 4000, query: str = "", scope: str = "global", subject: str = "") -> str:
        if self.storage is None:
            snapshot = self.snapshot()
            sections = [("USER.md", snapshot.user), ("MEMORY.md", snapshot.memory), ("SOUL.md", snapshot.soul)]
        else:
            items = self.search(query, scope=scope, subject=subject, limit=8)
            attributed = [(item["section"], f"[Subject: {item['subject'] or 'unspecified'}; confirmed: {item['confirmed_at']}; memory ID: {item['id']}] {item['content']}") for item in items]
            sections = [
                ("USER.md", "\n".join(text for section, text in attributed if section == "user")),
                ("MEMORY.md", "\n".join(text for section, text in attributed if section == "memory")),
                ("SOUL.md", self._read("soul")),
            ]
        defaults = {"USER.md": "No user preferences recorded.", "MEMORY.md": "No long-term facts recorded.", "SOUL.md": "No agent behavior guidance recorded."}
        return "\n\n".join(f"## {name}\n{limits.clip_text(_memory_body_or_default(text, defaults[name]), per_file_limit)}" for name, text in sections)

    def append(self, section: str, content: str, *, source: str = "manual", metadata: dict[str, Any] | None = None, created_at: str) -> str:
        key = self._normalize_section(section)
        clean = _sanitize_memory_text(content)
        if not clean:
            raise ValueError("memory content must be a non-empty string")
        metadata = dict(metadata or {})
        if key == "soul" and source != "manual":
            raise ValueError("Core identity may only be edited explicitly by the owner")
        if self.storage is not None and key != "soul":
            self.remember(
                clean, section=key, scope=str(metadata.get("session_id") or "global"),
                subject=str(metadata.get("user_id") or ""), confirmed_at=created_at,
                sources=[{"run_id": metadata.get("run_id", ""), "excerpt": limits.clip_text(str(metadata.get("user_message", clean)), 256)}],
            )
        else:
            path = self.memory_dir / MEMORY_FILES[key]
            text = path.read_text(encoding="utf-8")
            if clean not in text.splitlines():
                if len(text.encode()) + len(clean.encode()) > 16_000:
                    raise ValueError("Memory file budget exceeded")
                path.write_text(text.rstrip() + f"\n\n- {clean}\n", encoding="utf-8")
        if self.storage is not None:
            self.storage.insert_memory_event(section=key, content=clean, source=source, metadata=limits.compact_payload(metadata), created_at=created_at)
        return clean

    def remember(
        self, content: str, *, scope: str, subject: str = "", section: str = "memory",
        kind: str = "preference", memory_key: str = "", importance: float = 0.5,
        pinned: bool = False, expires_at: str | None = None, sources: list[dict[str, Any]] | None = None,
        confirmed_at: str | None = None, db: sqlite3.Connection | None = None,
    ) -> str:
        if self.storage is None:
            raise ValueError("Structured memory requires persistent storage")
        content = _sanitize_memory_text(content)
        if not content or len(content.encode()) > 1024:
            raise ValueError("Memory entries must contain 1 to 1024 UTF-8 bytes")
        if section not in {"user", "memory"} or kind not in {"ordinary", "preference", "episode", "commitment"}:
            raise ValueError("Invalid memory section or kind")
        if not isinstance(importance, (int, float)) or isinstance(importance, bool) or not math.isfinite(importance) or not 0 <= importance <= 1:
            raise ValueError("Memory importance must be between 0 and 1")
        if expires_at:
            expires_at = _timestamp(expires_at).isoformat()
        confirmed_at = _timestamp(confirmed_at or datetime.now(UTC).isoformat()).isoformat()
        memory_key = memory_key.strip() or hashlib.sha256(" ".join(content.lower().split()).encode()).hexdigest()[:24]
        if len(memory_key) > 100 or len(subject) > 100 or len(scope) > 200:
            raise ValueError("Memory identifiers are too long")
        sources = limits.compact_payload(sources or [])[-3:]
        if db is None:
            limits.ensure_storage_capacity(self.storage.data_dir, 8192)
            with self.storage.transaction() as transaction:
                return self.remember(content, scope=scope, subject=subject, section=section, kind=kind,
                    memory_key=memory_key, importance=importance, pinned=pinned, expires_at=expires_at,
                    sources=sources, confirmed_at=confirmed_at, db=transaction)
        existing = db.execute("SELECT * FROM memories WHERE scope=? AND subject=? AND (memory_key=? OR content=?)", (scope, subject, memory_key, content)).fetchone()
        digest = hashlib.sha256(content.lower().encode()).hexdigest()
        forgotten = db.execute("SELECT forgotten_at FROM memory_tombstones WHERE scope=? AND subject=? AND (memory_key=? OR content_hash=?) ORDER BY forgotten_at DESC LIMIT 1", (scope, subject, memory_key, digest)).fetchone()
        if forgotten and confirmed_at <= forgotten["forgotten_at"]:
            return ""
        identifier = existing["id"] if existing else uuid.uuid4().hex
        # Reflection and retrieval never change confirmation time without new source evidence.
        if existing and confirmed_at < existing["confirmed_at"]:
            return identifier
        if existing:
            previous_sources = json.loads(existing["sources_json"])
            sources = [item for item in previous_sources if item not in sources] + sources
            pinned = pinned or bool(existing["pinned"])
        now = datetime.now(UTC).isoformat()
        db.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET content=excluded.content, kind=excluded.kind, importance=excluded.importance, "
            "half_life_days=excluded.half_life_days, pinned=excluded.pinned, expires_at=excluded.expires_at, "
            "sources_json=excluded.sources_json, confirmed_at=excluded.confirmed_at, updated_at=excluded.updated_at",
            (identifier, scope, section, subject, memory_key, content, kind, importance,
             {"ordinary": 7, "preference": 90, "episode": 365, "commitment": 0}[kind], int(pinned), expires_at,
             json.dumps(sources[-3:], ensure_ascii=True), existing["created_at"] if existing else now, confirmed_at, now),
        )
        self._prune(db, datetime.now(UTC))
        return identifier

    def search(self, query: str, *, scope: str | None = None, subject: str = "", limit: int = 8, now: datetime | None = None) -> list[dict[str, Any]]:
        if self.storage is None:
            return []
        now = now or datetime.now(UTC)
        with self.storage.transaction() as db:
            if scope is None:
                rows = db.execute("SELECT * FROM memories").fetchall()
            else:
                rows = db.execute("SELECT * FROM memories WHERE scope IN (?, 'global')", (scope,)).fetchall()
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for row in rows:
            item = dict(row)
            strength = self.strength(item, now)
            if strength < 0.03:
                continue
            overlap = len(words & set(re.findall(r"[a-z0-9]+", (item["content"] + " " + item["memory_key"]).lower())))
            score = strength + min(overlap, 5) + (0.5 if subject and item["subject"] == subject else 0)
            item["strength"] = strength
            item["sources"] = json.loads(item.pop("sources_json"))
            scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1]["confirmed_at"]), reverse=True)
        result, size = [], 0
        for _, item in scored[:max(1, min(limit, 50))]:
            size += len(item["content"].encode())
            if size > limits.RECALL_BYTES:
                break
            result.append(item)
        return result

    @staticmethod
    def strength(item: dict[str, Any], now: datetime) -> float:
        if item["expires_at"] and _timestamp(item["expires_at"]) <= now:
            return 0
        if item["pinned"] or item["kind"] == "commitment":
            return 1
        days = max(0, (now - _timestamp(item["confirmed_at"])).total_seconds() / 86400)
        return float(item["importance"]) * 2 ** (-days / float(item["half_life_days"]))

    def forget(self, identifier: str, *, scope: str, include_global: bool = False) -> bool:
        if self.storage is None:
            return False
        with self.storage.transaction() as db:
            row = db.execute("SELECT * FROM memories WHERE id=?", (identifier,)).fetchone()
            if row is None or row["scope"] not in ({scope, "global"} if include_global else {scope}):
                return False
            db.execute("INSERT OR REPLACE INTO memory_tombstones VALUES (?, ?, ?, ?, ?)",
                       (row["scope"], row["subject"], row["memory_key"], hashlib.sha256(row["content"].lower().encode()).hexdigest(), datetime.now(UTC).isoformat()))
            db.execute("DELETE FROM memory_events WHERE content=?", (row["content"],))
            db.execute("DELETE FROM memories WHERE id=?", (identifier,))
        return True

    def maintain(self, *, now: datetime | None = None) -> None:
        if self.storage is not None:
            with self.storage.transaction() as db:
                self._prune(db, now or datetime.now(UTC))

    def _prune(self, db: sqlite3.Connection, now: datetime) -> None:
        db.execute("DELETE FROM memory_tombstones WHERE NOT EXISTS (SELECT 1 FROM provider_runs WHERE session_id=memory_tombstones.scope AND created_at <= memory_tombstones.forgotten_at)")
        rows = [dict(row) for row in db.execute("SELECT * FROM memories")]
        for item in rows:
            if self.strength(item, now) < 0.03:
                db.execute("DELETE FROM memories WHERE id=?", (item["id"],))
        rows = [dict(row) for row in db.execute("SELECT * FROM memories")]
        excess = len(rows) - limits.MAX_MEMORIES
        if excess > 0:
            candidates = sorted((item for item in rows if not item["pinned"] and item["kind"] != "commitment"), key=lambda item: self.strength(item, now))
            if len(candidates) < excess:
                raise limits.StorageLimitError("Memory budget is full of protected entries; remove or resolve an entry first.")
            db.executemany("DELETE FROM memories WHERE id=?", [(item["id"],) for item in candidates[:excess]])

    def _import_legacy(self) -> None:
        assert self.storage is not None
        with self.storage.transaction() as db:
            if db.execute("SELECT 1 FROM memory_state WHERE key='legacy_imported'").fetchone():
                return
            for section in ("user", "memory"):
                text = _memory_body_or_default(self._read(section), "")
                for line in text.splitlines():
                    clean = line.strip().removeprefix("- ")
                    if clean:
                        self.remember(limits.clip_text(clean, 1024), scope="global", section=section, db=db)
            db.execute("INSERT INTO memory_state VALUES ('legacy_imported', '1')")

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
            path.chmod(0o600)

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


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Memory timestamps require an explicit timezone")
    return parsed.astimezone(UTC)
