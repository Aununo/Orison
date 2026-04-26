from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from orison.config import load_config
from orison.core import SessionManager
from orison.providers.registry import build_provider_registry


@unittest.skipUnless(
    os.environ.get("ORISON_RUN_LIVE_CODEX") == "1",
    "set ORISON_RUN_LIVE_CODEX=1 to run the live openai-codex Dream smoke test",
)
class LiveOpenAICodexDreamSmokeTest(unittest.TestCase):
    def test_live_openai_codex_dream_writes_only_temp_memory_and_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            registry = build_provider_registry(load_config("config.toml"))
            manager = SessionManager(
                data_dir=root / "data",
                registry=registry,
                memory_dir=root / "memory",
                skills_dir=root / "skills",
            )
            now = datetime.now(UTC).isoformat()
            session = {
                "session_id": "live-dream-smoke",
                "provider_name": registry.default_name,
                "provider_session_id": "smoke-provider-session",
                "channel": "test",
                "user_id": "smoke-user",
                "created_at": now,
                "updated_at": now,
            }
            manager.storage.upsert_session(session)
            manager.storage.insert_run(
                run_id="smoke-run-1",
                session=session,
                user_message="我喜欢简洁的中文进度更新；重复任务：汇报 blockers、evidence、next action。",
                final_response="已记录：后续进度更新会简洁，并包含 blockers、evidence、next action。",
                error=None,
                provider_events=[],
                metadata={"source": "live_dream_smoke"},
                created_at=now,
            )

            result = manager.run_dream(limit=5)

            self.assertEqual(result.status, "success")
            self.assertIsNone(result.error)
            self.assertEqual(result.processed_run_count, 1)
            self.assertTrue(Path(result.changelog_path).exists())
            self.assertTrue(
                any(result.memory_updates.values()) or result.skills_written,
                "live Dream should apply at least one memory or skill update from the seeded smoke history",
            )


if __name__ == "__main__":
    unittest.main()
