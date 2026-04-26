from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orison.core import SessionManager
from orison.providers import ProviderHealth, ProviderRequest, ProviderRunResult
from orison.providers.registry import ProviderRegistry
from orison.skills import SkillRegistry


class CaptureProvider:
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.skill_selection_requests: list[ProviderRequest] = []
        self.skill_selection_response = '{"skills":["coding"]}'

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            self.skill_selection_requests.append(request)
            return ProviderRunResult(content=self.skill_selection_response)
        self.requests.append(request)
        return ProviderRunResult(content="ok", provider_session_id="thread-1")

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        self.requests.append(request)
        return ProviderRunResult(content="ok", provider_session_id=provider_session_id)

    async def stream(self, request: ProviderRequest):  # pragma: no cover
        return
        yield

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=True, message="ok")


class SkillRegistryTest(unittest.TestCase):
    def test_discovers_new_skill_and_extracts_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "foo" / "SKILL.md"
            path.parent.mkdir()
            path.write_text(
                "---\n"
                "name: foo\n"
                "description: Handle foo workflows.\n"
                "---\n\n"
                "# Foo\n\nFull instructions.\n",
                encoding="utf-8",
            )

            registry = SkillRegistry(tempdir)
            skills = registry.list_skills()

            self.assertEqual([skill.name for skill in skills], ["foo"])
            self.assertEqual(skills[0].description, "Handle foo workflows.")
            self.assertIn("foo: Handle foo workflows.", registry.summary())
            self.assertEqual(registry.load("foo").body, "# Foo\n\nFull instructions.")

    def test_ignores_skill_without_required_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "broken" / "SKILL.md"
            path.parent.mkdir()
            path.write_text("# Broken\n\nMissing metadata.\n", encoding="utf-8")

            registry = SkillRegistry(tempdir)

            self.assertEqual(registry.list_skills(), [])
            with self.assertRaises(KeyError):
                registry.load("broken")

    def test_context_for_names_loads_requested_skills_only(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            self._write_skill(tempdir, "coding", "Coding changes and tests.", "CODING FULL")
            self._write_skill(tempdir, "reflection", "Reflect on stable lessons.", "REFLECTION FULL")

            registry = SkillRegistry(tempdir)
            context = registry.context_for_names(["coding"])

            self.assertIn("CODING FULL", context)
            self.assertNotIn("REFLECTION FULL", context)

    def test_context_for_names_limits_loaded_skill_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            self._write_skill(tempdir, "coding", "Coding changes and tests.", "CODING FULL")
            self._write_skill(tempdir, "planning", "Planning changes and tests.", "PLANNING FULL")
            self._write_skill(tempdir, "reflection", "Reflection changes and tests.", "REFLECTION FULL")

            registry = SkillRegistry(tempdir)
            context = registry.context_for_names(["coding", "planning", "reflection"], max_skills=1)

            self.assertEqual(context.count("<skill name="), 1)

    def test_session_context_uses_provider_to_choose_relevant_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            skills_dir = Path(tempdir) / "skills"
            self._write_skill(skills_dir, "coding", "Coding changes and tests.", "CODING FULL")
            self._write_skill(skills_dir, "reflection", "Reflect on stable lessons.", "REFLECTION FULL")
            provider = CaptureProvider()
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=ProviderRegistry(default_name="fake", providers={"fake": provider}),
                memory_dir=Path(tempdir) / "memory",
                skills_dir=skills_dir,
            )

            manager.send_message("s1", "Please make a coding change", channel="local", user_id="u1")
            context = provider.requests[0].system_context

            self.assertIn("<skills_summary>", context)
            self.assertIn("- coding: Coding changes and tests.", context)
            self.assertIn("- reflection: Reflect on stable lessons.", context)
            self.assertIn("<relevant_skills>", context)
            self.assertIn("CODING FULL", context)
            self.assertNotIn("REFLECTION FULL", context)
            self.assertEqual(len(provider.skill_selection_requests), 1)
            self.assertIn("Return only strict JSON", provider.skill_selection_requests[0].system_context)

    def test_session_context_skips_skill_body_when_provider_selects_none(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            skills_dir = Path(tempdir) / "skills"
            self._write_skill(skills_dir, "coding", "Coding changes and tests.", "CODING FULL")
            provider = CaptureProvider()
            provider.skill_selection_response = '{"skills":[]}'
            manager = SessionManager(
                data_dir=Path(tempdir) / "data",
                registry=ProviderRegistry(default_name="fake", providers={"fake": provider}),
                memory_dir=Path(tempdir) / "memory",
                skills_dir=skills_dir,
            )

            manager.send_message("s1", "Please make a coding change", channel="local", user_id="u1")
            context = provider.requests[0].system_context

            self.assertIn("<skills_summary>", context)
            self.assertNotIn("<relevant_skills>", context)
            self.assertNotIn("CODING FULL", context)

    def test_bundled_audio_skill_was_removed_with_audio_modality(self) -> None:
        skills_dir = Path(__file__).resolve().parents[1] / "skills"
        registry = SkillRegistry(skills_dir)

        self.assertNotIn("audio", [skill.name for skill in registry.list_skills()])
        self.assertFalse((skills_dir / "audio" / "SKILL.md").exists())

    @staticmethod
    def _write_skill(root: str | Path, name: str, description: str, body: str) -> None:
        path = Path(root) / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name.title()}\n\n{body}\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
