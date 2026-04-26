from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orison.core.storage import CoreStorage
from orison.cron import CronManager, CronRunResult
from orison.tools import build_default_tool_registry
from orison.tools.base import Tool, object_schema


class NumericSchemaTool(Tool):
    @property
    def name(self) -> str:
        return "numeric_schema"

    @property
    def description(self) -> str:
        return "Validate numeric schema parameters."

    @property
    def parameters(self):
        return object_schema(
            {
                "count": {"type": "integer"},
                "score": {"type": "number"},
            }
        )

    async def execute(self, **kwargs):
        return kwargs


class ToolMigrationTest(unittest.TestCase):
    def test_tools_package_imports_without_core_import_order_dependency(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", "from orison.tools import build_default_tool_registry; print(build_default_tool_registry.__name__)"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("build_default_tool_registry", proc.stdout)

    def test_runtime_module_was_removed_after_tool_split(self) -> None:
        runtime_path = Path(__file__).resolve().parents[1] / "orison" / "tools" / "runtime.py"
        self.assertFalse(runtime_path.exists(), "tool implementations should live in focused modules, not runtime.py")

    def test_numeric_schema_rejects_bool_for_integer_and_number(self) -> None:
        tool = NumericSchemaTool()

        self.assertEqual(tool.validate_params(tool.cast_params({"count": True})), ["count should be integer"])
        self.assertEqual(tool.validate_params(tool.cast_params({"score": False})), ["score should be number"])

    def test_grep_supports_nanobot_advanced_options(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("alpha\nBeta\ngamma\n", encoding="utf-8")
            (root / "src" / "app.md").write_text("Beta\n", encoding="utf-8")
            registry = build_default_tool_registry(workspace=root, data_dir=root / ".orison")

            result = asyncio.run(
                registry.execute(
                    "grep",
                    {
                        "pattern": "beta",
                        "path": ".",
                        "type": "py",
                        "case_insensitive": "true",
                        "output_mode": "content",
                        "context_before": 1,
                        "context_after": 1,
                    },
                )
            )

            self.assertIn("src/app.py:2", result)
            self.assertIn("> 2| Beta", result)
            self.assertNotIn("app.md", result)

    def test_edit_file_uses_nanobot_tolerant_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = root / "quotes.txt"
            target.write_text("title\n  “hello”\n", encoding="utf-8")
            registry = build_default_tool_registry(workspace=root, data_dir=root / ".orison")

            result = asyncio.run(
                registry.execute(
                    "edit_file",
                    {
                        "path": "quotes.txt",
                        "old_text": '"hello"',
                        "new_text": '"goodbye"',
                    },
                )
            )

            self.assertIn("Successfully edited", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "title\n  “goodbye”\n")

    def test_message_queues_outbox_record(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            registry = build_default_tool_registry(workspace=root, data_dir=root / ".orison", session_id="s1")

            result = asyncio.run(
                registry.execute(
                    "message",
                    {"content": "<think>hidden</think>visible", "media": ["artifact.png"]},
                )
            )

            self.assertIn("Message queued at", result)
            outbox = list((root / ".orison" / "outbox").glob("*.json"))
            self.assertEqual(len(outbox), 1)
            payload = json.loads(outbox[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["session_id"], "s1")
            self.assertEqual(payload["content"], "visible")
            self.assertEqual(payload["media"], ["artifact.png"])

    def test_speech_tool_is_not_registered_after_audio_modality_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            registry = build_default_tool_registry(workspace=root, data_dir=root / ".orison", session_id="s1")

            self.assertNotIn("speech", registry.tool_names)
            self.assertFalse((Path(__file__).resolve().parents[1] / "orison" / "tools" / "speech.py").exists())

    def test_spawn_schedules_immediate_orison_cron_job(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            storage = CoreStorage(root / ".orison")

            def run_job(job):
                return CronRunResult(job=job, run_id="run-1", response="ok", error=None)

            cron = CronManager(storage, run_job)
            registry = build_default_tool_registry(
                workspace=root,
                data_dir=root / ".orison",
                cron=cron,
                session_id="session-main",
            )

            result = asyncio.run(registry.execute("spawn", {"task": "inspect project", "label": "inspect"}))

            self.assertIn("Spawned background task", result)
            jobs = cron.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].name, "inspect")
            self.assertEqual(jobs[0].prompt, "inspect project")
            self.assertTrue(jobs[0].target_session.startswith("session-main:spawn:"))

    def test_todo_tool_adds_lists_and_completes_user_scoped_items(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            storage = CoreStorage(root / ".orison")
            registry = build_default_tool_registry(
                workspace=root,
                data_dir=root / ".orison",
                storage=storage,
                session_id="qq:g:888:u:12345",
                context_metadata={
                    "channel": "qq",
                    "message_type": "group",
                    "group_id": 888,
                    "user_id": "12345",
                },
            )

            add_result = asyncio.run(
                registry.execute(
                    "todo",
                    {
                        "action": "add",
                        "title": "修 Orison todo",
                        "notes": "用 SQLite 持久化",
                        "due_at": "2026-04-27 21:00",
                        "priority": "high",
                    },
                )
            )

            self.assertIn("Created todo", add_result)
            todos = storage.list_todos(channel="qq", user_id="12345", group_id="888")
            self.assertEqual(len(todos), 1)
            todo = todos[0]
            self.assertEqual(todo["title"], "修 Orison todo")
            self.assertEqual(todo["notes"], "用 SQLite 持久化")
            self.assertEqual(todo["due_at"], "2026-04-27T21:00:00+08:00")
            self.assertEqual(todo["priority"], "high")
            self.assertEqual(todo["status"], "open")
            self.assertEqual(todo["session_id"], "qq:g:888:u:12345")
            self.assertEqual(todo["group_id"], "888")

            other_registry = build_default_tool_registry(
                workspace=root,
                data_dir=root / ".orison",
                storage=storage,
                session_id="qq:g:888:u:999",
                context_metadata={"channel": "qq", "message_type": "group", "group_id": 888, "user_id": "999"},
            )
            other_list = asyncio.run(other_registry.execute("todo", {"action": "list"}))
            self.assertIn("No open todos", other_list)

            list_result = asyncio.run(registry.execute("todo", {"action": "list"}))
            self.assertIn(todo["id"], list_result)
            self.assertIn("修 Orison todo", list_result)

            done_result = asyncio.run(registry.execute("todo", {"action": "done", "id": todo["id"]}))
            self.assertIn("Completed todo", done_result)
            self.assertEqual(storage.list_todos(channel="qq", user_id="12345", group_id="888", status="done")[0]["status"], "done")

    def test_todo_schema_restricts_status_and_priority_values(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            storage = CoreStorage(Path(tempdir) / ".orison")
            with storage._connect() as db:
                schema = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'todos'").fetchone()["sql"]

            self.assertIn("CHECK(status IN ('open', 'done', 'cancelled'))", schema)
            self.assertIn("CHECK(priority IN ('low', 'normal', 'high'))", schema)

            now = "2026-04-27T13:00:00+00:00"
            invalid_todo = {
                "id": "todo-invalid",
                "session_id": "s1",
                "channel": "qq",
                "user_id": "u1",
                "group_id": "g1",
                "title": "bad",
                "notes": "",
                "status": "weird",
                "priority": "urgent",
                "due_at": None,
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
            with self.assertRaises(sqlite3.IntegrityError):
                storage.create_todo(invalid_todo)

    def test_todo_tool_normalizes_due_at_and_rejects_malformed_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            storage = CoreStorage(root / ".orison")
            registry = build_default_tool_registry(
                workspace=root,
                data_dir=root / ".orison",
                storage=storage,
                session_id="qq:g:888:u:12345",
                context_metadata={"channel": "qq", "message_type": "group", "group_id": 888, "user_id": "12345"},
            )

            invalid_due = asyncio.run(registry.execute("todo", {"action": "add", "title": "bad due", "due_at": "not a date"}))
            self.assertIn("Error", invalid_due)
            self.assertIn("due_at", invalid_due)

            invalid_limit = asyncio.run(registry.execute("todo", {"action": "list", "limit": 0}))
            self.assertIn("limit must be >= 1", invalid_limit)

            invalid_priority = asyncio.run(registry.execute("todo", {"action": "add", "title": "bad", "priority": "urgent"}))
            self.assertIn("priority must be one of", invalid_priority)

            add_result = asyncio.run(
                registry.execute("todo", {"action": "add", "title": "带截止时间", "due_at": "2026-04-27 21:00"})
            )
            self.assertIn("Created todo", add_result)
            todo = storage.list_todos(channel="qq", user_id="12345", group_id="888")[0]
            self.assertEqual(todo["due_at"], "2026-04-27T21:00:00+08:00")

    def test_reminder_schedules_qq_group_mention_in_shanghai_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            storage = CoreStorage(root / ".orison")

            def run_job(job):
                return CronRunResult(job=job, run_id="run-1", response="ok", error=None)

            cron = CronManager(storage, run_job)
            registry = build_default_tool_registry(
                workspace=root,
                data_dir=root / ".orison",
                cron=cron,
                session_id="qq:g:888:u:12345",
                context_metadata={
                    "channel": "qq",
                    "message_type": "group",
                    "group_id": 888,
                    "user_id": "12345",
                },
            )

            result = asyncio.run(
                registry.execute(
                    "reminder",
                    {
                        "action": "add",
                        "message": "开会",
                        "remind_at": "2026-04-27 09:30",
                        "name": "会议提醒",
                    },
                )
            )

            self.assertIn("Created reminder", result)
            jobs = cron.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].name, "会议提醒")
            self.assertEqual(jobs[0].schedule, "once:2026-04-27T01:30:00+00:00")
            payload = json.loads(jobs[0].prompt.removeprefix("orison://reminder/"))
            self.assertEqual(payload["channel"], "qq")
            self.assertEqual(payload["message_type"], "group")
            self.assertEqual(payload["group_id"], "888")
            self.assertEqual(payload["user_id"], "12345")
            self.assertEqual(payload["message"], "开会")


if __name__ == "__main__":
    unittest.main()
