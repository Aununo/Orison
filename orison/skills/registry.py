"""SKILL.md discovery and context selection for Orison."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str

    def summary_line(self) -> str:
        return f"- {self.name}: {self.description}"


class SkillRegistry:
    """Loads SKILL.md files without binding skills to any provider."""

    def __init__(self, skills_dir: str | Path = "skills") -> None:
        self.skills_dir = Path(skills_dir)

    def list_skills(self) -> list[Skill]:
        if not self.skills_dir.exists():
            return []
        skills: list[Skill] = []
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            skill = self._load_skill(path)
            if skill is not None:
                skills.append(skill)
        return skills

    def summary(self) -> str:
        skills = self.list_skills()
        if not skills:
            return "No skills found."
        return "\n".join(skill.summary_line() for skill in skills)

    def context_within(self, max_bytes: int) -> str | None:
        """Return every skill's context, or None when the full set exceeds max_bytes."""
        skills = self.list_skills()
        context = self.context_for_names([skill.name for skill in skills], max_skills=len(skills))
        return context if len(context.encode("utf-8")) <= max_bytes else None

    def context_for_names(self, names: list[str], *, max_skills: int = 2) -> str:
        selected: list[Skill] = []
        seen: set[str] = set()
        for name in names:
            clean_name = name.strip()
            if not clean_name or clean_name in seen:
                continue
            try:
                selected.append(self.load(clean_name))
            except (KeyError, ValueError):
                continue
            seen.add(clean_name)
            if len(selected) >= max(0, max_skills):
                break

        return "\n\n".join(
            f"<skill name=\"{skill.name}\">\n{skill.body.strip()}\n</skill>"
            for skill in selected
        )

    def load(self, name: str) -> Skill:
        clean_name = name.strip()
        if not clean_name or "/" in clean_name or "\\" in clean_name:
            raise ValueError("skill name must be a simple directory name")
        path = self.skills_dir / clean_name / "SKILL.md"
        skill = self._load_skill(path)
        if skill is None:
            raise KeyError(f"skill not found: {clean_name}")
        return skill

    def _load_skill(self, path: Path) -> Skill | None:
        if not path.exists() or not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        metadata, body = _split_frontmatter(text)
        name = str(metadata.get("name") or "").strip()
        description = str(metadata.get("description") or "").strip()
        if not name or not description:
            return None
        return Skill(name=name, description=description, path=path, body=body.strip())


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw_meta = text[4:end].strip()
    body = text[end + 4 :].lstrip()
    metadata: dict[str, str] = {}
    for line in raw_meta.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            metadata[key] = value
    return metadata, body
