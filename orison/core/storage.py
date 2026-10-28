"""SQLite persistence for Orison Core."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orison import limits


class CoreStorage:
    """Small SQLite wrapper for sessions, messages, runs, events, and memory."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.data_dir / "orison.sqlite3"
        self._init_schema()
        self.db_path.chmod(0o600)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT session_id, provider_name, provider_session_id, channel,
                       user_id, created_at, updated_at
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def merge_family_sessions(self, group_id: str, member_ids: list[str]) -> None:
        canonical = f"qq:g:{group_id}"
        with self.transaction() as db:
            for member_id in member_ids:
                old_id = f"qq:{group_id}:{member_id}"
                row = db.execute("SELECT * FROM sessions WHERE session_id=?", (old_id,)).fetchone()
                if row is None:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO sessions VALUES (?, ?, NULL, ?, ?, ?, ?)",
                    (canonical, row["provider_name"], row["channel"], row["user_id"], row["created_at"], row["updated_at"]),
                )
                for table in ("messages", "provider_runs", "provider_events"):
                    db.execute(f"UPDATE {table} SET session_id=? WHERE session_id=?", (canonical, old_id))
                db.execute("DELETE FROM sessions WHERE session_id=?", (old_id,))

    def upsert_session(self, session: dict[str, Any]) -> None:
        limits.ensure_storage_capacity(self.data_dir, 4096)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sessions (
                    session_id, provider_name, provider_session_id, channel,
                    user_id, created_at, updated_at
                )
                VALUES (
                    :session_id, :provider_name, :provider_session_id, :channel,
                    :user_id, :created_at, :updated_at
                )
                ON CONFLICT(session_id) DO UPDATE SET
                    provider_name = excluded.provider_name,
                    provider_session_id = excluded.provider_session_id,
                    channel = excluded.channel,
                    user_id = excluded.user_id,
                    updated_at = excluded.updated_at
                """,
                session,
            )

    def insert_run(
        self,
        *,
        run_id: str,
        session: dict[str, Any],
        user_message: str,
        final_response: str,
        error: str | None,
        provider_events: list[dict[str, Any]],
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        limits.ensure_storage_capacity(self.data_dir, len(user_message.encode()) + len(final_response.encode()) + 32768)
        final_response = limits.clip_text(final_response, limits.MAX_MESSAGE_BYTES)
        metadata = limits.compact_payload(metadata)
        if len(_json(metadata).encode()) > 16384:
            metadata = {key: metadata[key] for key in ("group_id", "message_id", "speaker_id", "role", "name", "timestamp", "observe_only") if key in metadata}
        events, size = [], 0
        for event in provider_events[-100:]:
            event = limits.compact_payload(event)
            encoded = _json(event)
            if len(encoded.encode()) > 4096:
                event = {"type": event.get("type", ""), "detail": limits.clip_text(encoded, 3500)}
            size += len(_json(event).encode())
            if size > 65536:
                break
            events.append(event)
        provider_events = events
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, content, metadata_json, created_at
                )
                VALUES (?, ?, 'user', ?, ?, ?)
                """,
                (
                    f"{run_id}:user",
                    session["session_id"],
                    user_message,
                    _json(metadata),
                    created_at,
                ),
            )
            if final_response:
                db.execute(
                    """
                    INSERT INTO messages (
                        id, session_id, role, content, metadata_json, created_at
                    )
                    VALUES (?, ?, 'assistant', ?, ?, ?)
                    """,
                    (
                        f"{run_id}:assistant",
                        session["session_id"],
                        final_response,
                        _json({"run_id": run_id, "provider": session["provider_name"]}),
                        created_at,
                    ),
                )
            db.execute(
                """
                INSERT INTO provider_runs (
                    run_id, session_id, provider_name, provider_session_id,
                    channel, user_id, user_message, final_response, error,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session["session_id"],
                    session["provider_name"],
                    session.get("provider_session_id"),
                    session.get("channel", ""),
                    session.get("user_id", ""),
                    user_message,
                    final_response,
                    error,
                    _json(metadata),
                    created_at,
                ),
            )
            for index, event in enumerate(provider_events):
                db.execute(
                    """
                    INSERT INTO provider_events (
                        run_id, session_id, event_index, event_type,
                        event_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        session["session_id"],
                        index,
                        str(event.get("type", "")),
                        _json(event),
                        created_at,
                    ),
                )

    def list_run_records(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT run_id, session_id, provider_name, provider_session_id,
                       channel, user_id, user_message, final_response, error,
                       metadata_json, created_at
                FROM provider_runs
                WHERE session_id = ?
                ORDER BY created_at, run_id
                """,
                (session_id,),
            ).fetchall()
            event_rows = db.execute(
                """
                SELECT run_id, event_json
                FROM provider_events
                WHERE session_id = ?
                ORDER BY run_id, event_index
                """,
                (session_id,),
            ).fetchall()

        events_by_run: dict[str, list[dict[str, Any]]] = {}
        for row in event_rows:
            events_by_run.setdefault(row["run_id"], []).append(json.loads(row["event_json"]))

        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["metadata"] = json.loads(record.pop("metadata_json"))
            record["provider_events"] = events_by_run.get(record["run_id"], [])
            records.append(record)
        return records

    def list_recent_run_records(
        self,
        *,
        after_created_at: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if after_created_at:
            where = "WHERE created_at > ?"
            params.append(after_created_at)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT run_id, session_id, provider_name, provider_session_id,
                       channel, user_id, user_message, final_response, error,
                       metadata_json, created_at
                FROM provider_runs
                {where}
                ORDER BY created_at, run_id
                LIMIT ?
                """,
                params,
            ).fetchall()

        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["metadata"] = json.loads(record.pop("metadata_json"))
            records.append(record)
        return records

    def search_history(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT session_id, role, content, metadata_json, created_at
                FROM messages
                WHERE content LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def conversation_runs(
        self, *, session_id: str | None = None, after: str | None = None,
        pending_only: bool = False, newest: bool = False, limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if after:
            timestamp, run_id = json.loads(after)
            clauses.append("(created_at, run_id) > (?, ?)")
            params.extend((timestamp, run_id))
        if pending_only:
            clauses.append("dream_processed = 0")
        direction = "DESC" if newest else "ASC"
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM provider_runs WHERE {' AND '.join(clauses) or '1'} "
                f"ORDER BY created_at {direction}, run_id {direction} LIMIT ?",
                (*params, max(1, min(limit, 1000))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return list(reversed(result)) if newest else result

    def user_run_activity(self, session_id: str, user_id: str, *, since: str) -> tuple[int, str | None]:
        """Count a speaker's runs since a UTC timestamp and return their latest run time."""
        with self._connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM provider_runs WHERE session_id = ? AND user_id = ? AND created_at >= ?",
                (session_id, user_id, since),
            ).fetchone()[0]
            latest = db.execute(
                "SELECT MAX(created_at) FROM provider_runs WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()[0]
        return count, latest

    def get_conversation_summary(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM conversation_summaries WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def save_conversation_summary(self, session_id: str, content: str, state: dict[str, Any], through: str, now: str) -> None:
        limits.ensure_storage_capacity(self.data_dir, 8192)
        with self.transaction() as db:
            previous = db.execute("SELECT through_cursor FROM conversation_summaries WHERE session_id=?", (session_id,)).fetchone()
            if previous and tuple(json.loads(previous["through_cursor"])) >= tuple(json.loads(through)):
                return
            db.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET content=excluded.content, state_json=excluded.state_json, "
                "through_cursor=excluded.through_cursor, updated_at=excluded.updated_at",
                (session_id, content, _json(state), through, now),
            )

    @contextmanager
    def transaction(self):
        db = self._connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def maintain(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        cutoff = (now - timedelta(days=14)).isoformat()
        debug_cutoff = (now - timedelta(days=3)).isoformat()
        month_cutoff = (now - timedelta(days=30)).isoformat()
        with self.transaction() as db:
            db.execute("DELETE FROM provider_events WHERE created_at < ?", (debug_cutoff,))
            db.execute("DELETE FROM memory_events WHERE created_at < ?", (month_cutoff,))
            db.execute("DELETE FROM dream_runs WHERE completed_at < ?", (month_cutoff,))
            db.execute("DELETE FROM daily_summaries WHERE updated_at < ?", (month_cutoff,))
            db.execute("CREATE TEMP TABLE retired_runs AS SELECT run_id FROM provider_runs WHERE dream_processed = 1 AND created_at < ?", (cutoff,))
            db.execute("DELETE FROM provider_events WHERE run_id IN (SELECT run_id FROM retired_runs)")
            db.execute("DELETE FROM messages WHERE id IN (SELECT run_id || ':user' FROM retired_runs UNION SELECT run_id || ':assistant' FROM retired_runs)")
            retired = db.execute("DELETE FROM provider_runs WHERE run_id IN (SELECT run_id FROM retired_runs)").rowcount
            db.execute("DROP TABLE retired_runs")
        for path in (self.data_dir / "dream" / "changelog").glob("*.md"):
            if not path.is_symlink() and path.stat().st_mtime < (now - timedelta(days=30)).timestamp():
                path.unlink()
        with self._connect() as db:
            free = db.execute("PRAGMA freelist_count").fetchone()[0]
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            # Reuse free pages normally; shrink only when substantial space is unused.
            if pages > 256 and free > pages // 4 and limits.runtime_size(self.data_dir) + self.db_path.stat().st_size * 2 < limits.STORAGE_BYTES:
                db.execute("VACUUM")
        return {"retired_runs": retired}

    def insert_memory_event(
        self,
        *,
        section: str,
        content: str,
        source: str,
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO memory_events (
                    section, content, source, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (section, content, source, _json(metadata), created_at),
            )

    def create_todo(self, todo: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO todos (
                    id, session_id, channel, user_id, group_id, title, notes,
                    status, priority, due_at, created_at, updated_at, completed_at
                )
                VALUES (
                    :id, :session_id, :channel, :user_id, :group_id, :title, :notes,
                    :status, :priority, :due_at, :created_at, :updated_at, :completed_at
                )
                """,
                todo,
            )

    def list_todos(
        self,
        *,
        channel: str = "",
        user_id: str = "",
        group_id: str = "",
        status: str | None = "open",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["channel = ?", "user_id = ?", "group_id = ?"]
        params: list[Any] = [channel, user_id, group_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT id, session_id, channel, user_id, group_id, title, notes,
                       status, priority, due_at, created_at, updated_at, completed_at
                FROM todos
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                    COALESCE(due_at, ''), created_at, id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_todo(
        self,
        todo_id: str,
        *,
        channel: str,
        user_id: str,
        group_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        allowed = {"title", "notes", "status", "priority", "due_at", "updated_at", "completed_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_todo(todo_id, channel=channel, user_id=user_id, group_id=group_id)
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        params = {
            **updates,
            "id": todo_id,
            "channel": channel,
            "user_id": user_id,
            "group_id": group_id,
        }
        with self._connect() as db:
            db.execute(
                f"""
                UPDATE todos
                SET {assignments}
                WHERE id = :id AND channel = :channel AND user_id = :user_id AND group_id = :group_id
                """,
                params,
            )
        return self.get_todo(todo_id, channel=channel, user_id=user_id, group_id=group_id)

    def delete_todo(self, todo_id: str, *, channel: str, user_id: str, group_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM todos WHERE id = ? AND channel = ? AND user_id = ? AND group_id = ?",
                (todo_id, channel, user_id, group_id),
            )
            return cursor.rowcount > 0

    def get_todo(self, todo_id: str, *, channel: str, user_id: str, group_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT id, session_id, channel, user_id, group_id, title, notes,
                       status, priority, due_at, created_at, updated_at, completed_at
                FROM todos
                WHERE id = ? AND channel = ? AND user_id = ? AND group_id = ?
                """,
                (todo_id, channel, user_id, group_id),
            ).fetchone()
        return dict(row) if row else None

    def list_cron_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, name, prompt, schedule, provider, target_session,
                       enabled, last_run_at, next_run_at, last_status,
                       last_error, created_at, updated_at
                FROM cron_jobs
                ORDER BY created_at, id
                """
            ).fetchall()
        return [_cron_job_from_row(row) for row in rows]

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT id, name, prompt, schedule, provider, target_session,
                       enabled, last_run_at, next_run_at, last_status,
                       last_error, created_at, updated_at
                FROM cron_jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return _cron_job_from_row(row) if row else None

    def upsert_cron_job(self, job: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO cron_jobs (
                    id, name, prompt, schedule, provider, target_session,
                    enabled, last_run_at, next_run_at, last_status,
                    last_error, created_at, updated_at
                )
                VALUES (
                    :id, :name, :prompt, :schedule, :provider, :target_session,
                    :enabled, :last_run_at, :next_run_at, :last_status,
                    :last_error, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    prompt = excluded.prompt,
                    schedule = excluded.schedule,
                    provider = excluded.provider,
                    target_session = excluded.target_session,
                    enabled = excluded.enabled,
                    last_run_at = excluded.last_run_at,
                    next_run_at = excluded.next_run_at,
                    last_status = excluded.last_status,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                {
                    **job,
                    "enabled": 1 if job.get("enabled") else 0,
                },
            )

    def delete_cron_job(self, job_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    def due_cron_jobs(self, now: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, name, prompt, schedule, provider, target_session,
                       enabled, last_run_at, next_run_at, last_status,
                       last_error, created_at, updated_at
                FROM cron_jobs
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at, id
                """,
                (now,),
            ).fetchall()
        return [_cron_job_from_row(row) for row in rows]

    def latest_dream_cursor(self) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT cursor_after
                FROM dream_runs
                WHERE status = 'success'
                ORDER BY completed_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        return str(row["cursor_after"]) if row and row["cursor_after"] else None

    def insert_dream_run(
        self,
        *,
        run_id: str,
        provider_name: str,
        status: str,
        cursor_before: str | None,
        cursor_after: str | None,
        processed_run_count: int,
        summary: str,
        changelog_path: str,
        rejected_changes: list[str],
        error: str | None,
        raw_response: str,
        started_at: str,
        completed_at: str,
        db: sqlite3.Connection | None = None,
    ) -> None:
        with (nullcontext(db) if db is not None else self._connect()) as db:
            db.execute(
                """
                INSERT INTO dream_runs (
                    run_id, provider_name, status, cursor_before, cursor_after,
                    processed_run_count, summary, changelog_path,
                    rejected_changes_json, error, raw_response,
                    started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    provider_name,
                    status,
                    cursor_before,
                    cursor_after,
                    processed_run_count,
                    summary,
                    changelog_path,
                    _json_list(rejected_changes),
                    error,
                    raw_response,
                    started_at,
                    completed_at,
                ),
            )

    def list_dream_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT run_id, provider_name, status, cursor_before, cursor_after,
                       processed_run_count, summary, changelog_path,
                       rejected_changes_json, error, raw_response,
                       started_at, completed_at
                FROM dream_runs
                ORDER BY completed_at DESC, run_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["rejected_changes"] = json.loads(record.pop("rejected_changes_json"))
            records.append(record)
        return records

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        db.execute(f"PRAGMA max_page_count = {max(1, limits.STORAGE_BYTES // page_size)}")
        return db

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    provider_name TEXT NOT NULL,
                    provider_session_id TEXT,
                    channel TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS provider_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    provider_session_id TEXT,
                    channel TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    user_message TEXT NOT NULL,
                    final_response TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS provider_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL DEFAULT '',
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES provider_runs(run_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    section TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    memory_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    importance REAL NOT NULL,
                    half_life_days REAL NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope, subject, memory_key)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, subject);

                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    session_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    through_cursor TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_summaries (
                    scope TEXT NOT NULL,
                    day TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope, day)
                );
                CREATE TABLE IF NOT EXISTS memory_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_tombstones (
                    scope TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    forgotten_at TEXT NOT NULL,
                    PRIMARY KEY(scope, subject, memory_key)
                );

                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    group_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'cancelled')),
                    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high')),
                    due_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_todos_scope_status
                ON todos(channel, user_id, group_id, status, created_at);

                CREATE TABLE IF NOT EXISTS cron_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    provider TEXT,
                    target_session TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    last_status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dream_runs (
                    run_id TEXT PRIMARY KEY,
                    provider_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cursor_before TEXT,
                    cursor_after TEXT,
                    processed_run_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    changelog_path TEXT NOT NULL DEFAULT '',
                    rejected_changes_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    raw_response TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                """
            )
            self._ensure_todos_constraints(db)
            columns = {row[1] for row in db.execute("PRAGMA table_info(provider_runs)")}
            if "dream_processed" not in columns:
                db.execute("ALTER TABLE provider_runs ADD COLUMN dream_processed INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runs_pending ON provider_runs(dream_processed, created_at, run_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runs_session ON provider_runs(session_id, created_at, run_id)")

    def _ensure_todos_constraints(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'todos'").fetchone()
        schema = str(row["sql"] if row else "")
        if "CHECK(status IN ('open', 'done', 'cancelled'))" in schema and "CHECK(priority IN ('low', 'normal', 'high'))" in schema:
            return
        db.executescript(
            """
            ALTER TABLE todos RENAME TO todos_legacy;
            CREATE TABLE todos (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'cancelled')),
                priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high')),
                due_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            INSERT INTO todos (
                id, session_id, channel, user_id, group_id, title, notes,
                status, priority, due_at, created_at, updated_at, completed_at
            )
            SELECT
                id, session_id, channel, user_id, group_id, title, notes,
                CASE WHEN status IN ('open', 'done', 'cancelled') THEN status ELSE 'open' END,
                CASE WHEN priority IN ('low', 'normal', 'high') THEN priority ELSE 'normal' END,
                due_at, created_at, updated_at, completed_at
            FROM todos_legacy;
            DROP TABLE todos_legacy;
            CREATE INDEX IF NOT EXISTS idx_todos_scope_status
            ON todos(channel, user_id, group_id, status, created_at);
            """
        )


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_list(payload: list[str]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _cron_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job["enabled"] = bool(job["enabled"])
    return job
