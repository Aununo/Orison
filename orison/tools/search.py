"""Workspace search tools."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from orison.tools.base import object_schema
from orison.tools.common import (
    WorkspaceTool,
    _IGNORE_DIRS,
    _display_path,
    _format_grep_block,
    _is_binary,
    _iter_files,
    _match_glob,
    _matches_type,
    _truncate,
)

class GlobTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "Find workspace paths matching a glob pattern, newest first."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "pattern": {"type": "string", "minLength": 1},
                "path": {"type": "string", "description": "Directory to search from, default '.'"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Legacy alias for head_limit"},
                "head_limit": {"type": "integer", "minimum": 0, "maximum": 1000},
                "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                "entry_type": {"type": "string", "enum": ["files", "dirs", "both"]},
            },
            required=["pattern"],
        )

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        entry_type: str = "files",
        **_: Any,
    ) -> str:
        try:
            root = self._resolve(path)
            if not root.exists():
                return f"Error: Path not found: {path}"
            if not root.is_dir():
                return f"Error: Not a directory: {path}"
            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif max_results is not None:
                limit = max_results
            else:
                limit = 250
            include_files = entry_type in {"files", "both"}
            include_dirs = entry_type in {"dirs", "both"}
            matches: list[Path] = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS)
                current = Path(dirpath)
                names = (sorted(dirnames) if include_dirs else []) + (sorted(filenames) if include_files else [])
                for name in names:
                    target = current / name
                    rel = target.relative_to(root).as_posix()
                    if _match_glob(rel, name, pattern):
                        matches.append(target)
            matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            shown = matches[offset:] if limit is None else matches[offset : offset + limit]
            lines = [_display_path(p, self.workspace) + ("/" if p.is_dir() else "") for p in shown]
            if len(matches) > offset + len(shown):
                lines.append(f"(pagination: limit={limit}, offset={offset})")
            elif offset:
                lines.append(f"(pagination: offset={offset})")
            return "\n".join(lines) if lines else f"No paths matched pattern '{pattern}' in {path}"
        except Exception as exc:
            return f"Error running glob: {exc}"


class GrepTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search workspace files with a regex or fixed string. Default output_mode is "
            "files_with_matches; use content for matching lines with optional context."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "pattern": {"type": "string", "minLength": 1},
                "path": {"type": "string", "description": "File or directory, default '.'"},
                "glob": {"type": "string", "description": "Optional file glob filter"},
                "type": {"type": "string", "description": "Optional file type shorthand, e.g. py, ts, md"},
                "ignore_case": {"type": "boolean"},
                "case_insensitive": {"type": "boolean"},
                "fixed_strings": {"type": "boolean"},
                "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                "context_before": {"type": "integer", "minimum": 0, "maximum": 20},
                "context_after": {"type": "integer", "minimum": 0, "maximum": 20},
                "max_matches": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
                "head_limit": {"type": "integer", "minimum": 0, "maximum": 1000},
                "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
            },
            required=["pattern"],
        )

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        ignore_case: bool = False,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **_: Any,
    ) -> str:
        try:
            flags = re.IGNORECASE if ignore_case or case_insensitive else 0
            rx = re.compile(re.escape(pattern) if fixed_strings else pattern, flags)
            root = self._resolve(path)
            if not root.exists():
                return f"Error: Path not found: {path}"
            files = [root] if root.is_file() else _iter_files(root)
            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = 250
            blocks: list[str] = []
            matching_files: list[str] = []
            counts: dict[str, int] = {}
            mtimes: dict[str, float] = {}
            seen_content = 0
            skipped_binary = 0
            skipped_large = 0
            truncated = False
            for file_path in files:
                rel = _display_path(file_path, self.workspace)
                if glob and not _match_glob(rel, file_path.name, glob):
                    continue
                if not _matches_type(file_path.name, type):
                    continue
                try:
                    raw = file_path.read_bytes()
                except OSError:
                    continue
                if len(raw) > 2_000_000:
                    skipped_large += 1
                    continue
                if _is_binary(raw):
                    skipped_binary += 1
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue
                lines = text.splitlines()
                file_had_match = False
                for idx, line in enumerate(lines, 1):
                    if rx.search(line):
                        file_had_match = True
                        if output_mode == "count":
                            counts[rel] = counts.get(rel, 0) + 1
                            continue
                        if output_mode == "files_with_matches":
                            if rel not in matching_files:
                                matching_files.append(rel)
                                mtimes[rel] = file_path.stat().st_mtime
                            break
                        seen_content += 1
                        if seen_content <= offset:
                            continue
                        if limit is not None and len(blocks) >= limit:
                            truncated = True
                            break
                        blocks.append(_format_grep_block(rel, lines, idx, context_before, context_after))
                if file_had_match and output_mode == "count":
                    if rel not in matching_files:
                        matching_files.append(rel)
                        mtimes[rel] = file_path.stat().st_mtime
                if truncated:
                    break
            notes: list[str] = []
            if output_mode == "files_with_matches":
                ordered = sorted(matching_files, key=lambda name: (-mtimes.get(name, 0.0), name))
                paged = ordered[offset:] if limit is None else ordered[offset : offset + limit]
                result = "\n".join(paged) if paged else f"No matches found for pattern '{pattern}' in {path}"
                if len(ordered) > offset + len(paged):
                    notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif output_mode == "count":
                ordered = sorted(matching_files, key=lambda name: (-mtimes.get(name, 0.0), name))
                paged = ordered[offset:] if limit is None else ordered[offset : offset + limit]
                result = "\n".join(f"{name}: {counts[name]}" for name in paged) if paged else f"No matches found for pattern '{pattern}' in {path}"
                notes.append(f"(total matches: {sum(counts.values())} in {len(counts)} files)")
            else:
                result = "\n\n".join(blocks) if blocks else f"No matches found for pattern '{pattern}' in {path}"
                if truncated:
                    notes.append(f"(pagination: limit={limit}, offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if notes:
                result += "\n\n" + "\n".join(notes)
            return _truncate(result, 128_000)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"
        except Exception as exc:
            return f"Error running grep: {exc}"


