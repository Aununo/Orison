from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PhasePlanTest(unittest.TestCase):
    def test_phase_0_required_structure_exists(self) -> None:
        required_paths = [
            "orison/core",
            "orison/providers",
            "orison/providers/openai_codex.py",
            "orison/providers/openai.py",
            "orison/providers/deepseek.py",
            "orison/providers/kimi.py",
            "orison/providers/local_openai_compatible.py",
            "orison/memory",
            "orison/skills",
            "orison/cron",
            "orison/dream",
            "orison/server.py",
            "cmd/orison-channel",
            "channels/core",
            "channels/client",
            "channels/local",
            "channels/qq",
            "config.toml",
            ".env.example",
            "README.md",
        ]

        missing = [path for path in required_paths if not (ROOT / path).exists()]

        self.assertEqual(missing, [])

    def test_codex_cli_calls_stay_inside_openai_codex_provider(self) -> None:
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".omx", ".orison", "__pycache__", "tests", "docs"} for part in path.parts):
                continue
            if path.name in {"README.md", "config.toml"}:
                continue
            if path.relative_to(ROOT).as_posix() == "orison/providers/openai_codex.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "codex exec" in text:
                offenders.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(offenders, [])

    def test_phase_10_reserved_provider_modules_exist(self) -> None:
        required_providers = {
            "openai": "orison/providers/openai.py",
            "deepseek": "orison/providers/deepseek.py",
            "kimi": "orison/providers/kimi.py",
            "local-openai-compatible": "orison/providers/local_openai_compatible.py",
        }

        for name, path in required_providers.items():
            with self.subTest(provider=name):
                text = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("Reserved", text)
                self.assertNotIn("codex exec", text)


if __name__ == "__main__":
    unittest.main()
