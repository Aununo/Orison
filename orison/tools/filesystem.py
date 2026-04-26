"""Workspace filesystem tools."""

from __future__ import annotations

import base64
import difflib
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orison.tools.base import object_schema
from orison.tools.common import (
    WorkspaceTool,
    _IGNORE_DIRS,
    _detect_image_mime,
    _display_path,
    _empty_notebook,
    _format_pdf_parts,
    _html_to_markdown,
    _html_to_text,
    _is_binary,
    _is_blocked_device,
    _iter_files,
    _match_glob,
    _new_cell,
    _normalize,
    _orison_schedule,
    _read_notebook,
    _read_pdf_text,
    _strip_tags,
    _truncate,
    _validate_resolved_url,
    _validate_url,
    _validate_url_safe,
)

class ReadFileTool(WorkspaceTool):
    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file. UTF-8 text is returned with line numbers; images return metadata "
            "and base64 content; PDFs return extracted text when a PDF library is available. "
            "Use offset/limit for text and pages for PDFs."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "offset": {"type": "integer", "minimum": 1, "description": "1-indexed start line"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
                "pages": {"type": "string", "description": "PDF page range, e.g. '1-5'"},
            },
            required=["path"],
        )

    async def execute(self, path: str, offset: int = 1, limit: int | None = None, pages: str | None = None, **_: Any) -> Any:
        if _is_blocked_device(path):
            return f"Error: Reading {path} is blocked"
        try:
            target = self._resolve(path)
            if _is_blocked_device(target):
                return f"Error: Reading {target} is blocked"
            if not target.exists():
                return f"Error: File not found: {path}"
            if not target.is_file():
                return f"Error: Not a file: {path}"
            if target.suffix.lower() == ".pdf":
                return _read_pdf_text(target, pages, max_pages=self._MAX_PDF_PAGES, max_chars=self._MAX_CHARS)
            raw = target.read_bytes()
        except Exception as exc:
            return f"Error reading file: {exc}"
        if not raw:
            return f"(Empty file: {path})"
        mime = _detect_image_mime(raw) or mimetypes.guess_type(str(target))[0]
        if mime and mime.startswith("image/"):
            return {
                "type": "image",
                "mime_type": mime,
                "path": str(target),
                "note": f"(Image file: {path})",
                "data": base64.b64encode(raw).decode("ascii"),
            }
        if mime and not mime.startswith("text/") and target.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".md", ".json", ".toml", ".yaml", ".yml", ".txt", ".html", ".css", ".sh", ".csv"}:
            return f"Error: Cannot read binary file {path} (MIME: {mime})"
        try:
            text = raw.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            return f"Error: Cannot read binary file {path}; only UTF-8 text is supported"
        lines = text.splitlines()
        if offset > len(lines) and lines:
            return f"Error: offset {offset} is beyond end of file ({len(lines)} lines)"
        start = max(offset, 1) - 1
        stop = min(start + min(limit or self._DEFAULT_LIMIT, 5000), len(lines))
        rendered = [f"{idx + 1}| {lines[idx]}" for idx in range(start, stop)]
        result = "\n".join(rendered)
        if len(result) > self._MAX_CHARS:
            result = result[: self._MAX_CHARS] + "\n\n(Text truncated at ~128K chars)"
        suffix = (
            f"\n\n(Showing lines {offset}-{stop} of {len(lines)}. Use offset={stop + 1} to continue.)"
            if stop < len(lines)
            else f"\n\n(End of file - {len(lines)} lines total)"
        )
        return result + suffix


class WriteFileTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write complete file content inside the workspace, creating parent directories when needed."

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
            },
            required=["path", "content"],
        )

    async def execute(self, path: str, content: str, **_: Any) -> str:
        try:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {target}"
        except Exception as exc:
            return f"Error writing file: {exc}"


_QUOTE_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


@dataclass(slots=True)
class _MatchSpan:
    start: int
    end: int
    text: str
    line: int


def _normalize_quotes(text: str) -> str:
    return text.translate(_QUOTE_TABLE)


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = content.find(old_text, start)
        if idx == -1:
            break
        matches.append(_MatchSpan(idx, idx + len(old_text), content[idx : idx + len(old_text)], content.count("\n", 0, idx) + 1))
        start = idx + max(1, len(old_text))
    return matches


def _find_trim_matches(content: str, old_text: str, *, normalize_quotes: bool = False) -> list[_MatchSpan]:
    old_lines = old_text.splitlines()
    if not old_lines:
        return []
    content_lines = content.splitlines()
    keepends = content.splitlines(keepends=True)
    if len(content_lines) < len(old_lines):
        return []
    offsets: list[int] = []
    pos = 0
    for line in keepends:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)
    expected = [_normalize_quotes(line.strip()) if normalize_quotes else line.strip() for line in old_lines]
    matches: list[_MatchSpan] = []
    width = len(expected)
    for idx in range(len(content_lines) - width + 1):
        window = content_lines[idx : idx + width]
        actual = [_normalize_quotes(line.strip()) if normalize_quotes else line.strip() for line in window]
        if actual != expected:
            continue
        start = offsets[idx]
        end = offsets[idx + width]
        if keepends[idx + width - 1].endswith("\n"):
            end -= 1
        matches.append(_MatchSpan(start, end, content[start:end], idx + 1))
    return matches


def _find_quote_matches(content: str, old_text: str) -> list[_MatchSpan]:
    normalized_content = _normalize_quotes(content)
    normalized_old = _normalize_quotes(old_text)
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = normalized_content.find(normalized_old, start)
        if idx == -1:
            break
        matches.append(_MatchSpan(idx, idx + len(old_text), content[idx : idx + len(old_text)], content.count("\n", 0, idx) + 1))
        start = idx + max(1, len(normalized_old))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    for matcher in (
        lambda: _find_exact_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text, normalize_quotes=True),
        lambda: _find_quote_matches(content, old_text),
    ):
        matches = matcher()
        if matches:
            return matches
    return []


def _curly_double_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for char in text:
        if char == '"':
            parts.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            parts.append(char)
    return "".join(parts)


def _curly_single_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for idx, char in enumerate(text):
        if char != "'":
            parts.append(char)
            continue
        prev_char = text[idx - 1] if idx > 0 else ""
        next_char = text[idx + 1] if idx + 1 < len(text) else ""
        if prev_char.isalnum() and next_char.isalnum():
            parts.append("\u2019")
            continue
        parts.append("\u2018" if opening else "\u2019")
        opening = not opening
    return "".join(parts)


def _preserve_quote_style(old_text: str, actual_text: str, new_text: str) -> str:
    if _normalize_quotes(old_text.strip()) != _normalize_quotes(actual_text.strip()) or old_text == actual_text:
        return new_text
    styled = new_text
    if any(char in actual_text for char in ("\u201c", "\u201d")) and '"' in styled:
        styled = _curly_double_quotes(styled)
    if any(char in actual_text for char in ("\u2018", "\u2019")) and "'" in styled:
        styled = _curly_single_quotes(styled)
    return styled


def _reindent_like_match(old_text: str, actual_text: str, new_text: str) -> str:
    old_lines = old_text.split("\n")
    actual_lines = actual_text.split("\n")
    if len(old_lines) != len(actual_lines):
        return new_text
    comparable = [
        (old_line, actual_line)
        for old_line, actual_line in zip(old_lines, actual_lines)
        if old_line.strip() and actual_line.strip()
    ]
    if not comparable:
        return new_text
    if any(_normalize_quotes(old.strip()) != _normalize_quotes(actual.strip()) for old, actual in comparable):
        return new_text
    old_ws = _leading_ws(comparable[0][0])
    actual_ws = _leading_ws(comparable[0][1])
    if actual_ws == old_ws:
        return new_text
    delta = actual_ws[len(old_ws) :] if old_ws and actual_ws.startswith(old_ws) else (actual_ws if not old_ws else "")
    if not delta:
        return new_text
    return "\n".join((delta + line) if line else line for line in new_text.split("\n"))


def _best_window(old_text: str, content: str) -> tuple[float, int, list[str], list[str]]:
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))
    best_ratio = -1.0
    best_start = 0
    best_lines: list[str] = []
    for idx in range(max(1, len(lines) - window + 1)):
        current = lines[idx : idx + window]
        ratio = difflib.SequenceMatcher(None, old_lines, current).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = idx
            best_lines = current
    actual = "".join(best_lines).replace("\r\n", "\n").rstrip("\n")
    old = old_text.replace("\r\n", "\n").rstrip("\n")
    hints: list[str] = []
    if old.lower() == actual.lower() and old != actual:
        hints.append("letter case differs")
    if "\n".join(" ".join(line.split()) for line in old.splitlines()) == "\n".join(" ".join(line.split()) for line in actual.splitlines()) and old != actual:
        hints.append("whitespace differs")
    if _normalize_quotes(old) == _normalize_quotes(actual) and old != actual:
        hints.append("quote style differs")
    return best_ratio, best_start, best_lines, hints


def _file_not_found_msg(path: str, target: Path) -> str:
    suggestions: list[str] = []
    if target.parent.is_dir():
        close = difflib.get_close_matches(
            target.name,
            [child.name for child in target.parent.iterdir() if child.is_file()],
            n=3,
            cutoff=0.6,
        )
        suggestions = [str(target.parent / name) for name in close]
    if suggestions:
        return f"Error: File not found: {path}\nDid you mean: {', '.join(suggestions)}?"
    return f"Error: File not found: {path}"


def _not_found_msg(old_text: str, content: str, path: str) -> str:
    best_ratio, best_start, best_lines, hints = _best_window(old_text, content)
    if best_ratio > 0.5:
        diff = "\n".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            )
        )
        hint = f"\nPossible cause: {', '.join(hints)}." if hints else ""
        return (
            f"Error: old_text not found in {path}."
            f"{hint}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        )
    return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


class EditFileTool(WorkspaceTool):
    _MARKDOWN_EXTS = {".md", ".mdx", ".markdown"}
    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace text in a workspace file. Tolerates minor indentation/whitespace "
            "differences and curly/straight quote mismatches. old_text='' creates a missing file."
        )

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences"},
            },
            required=["path", "old_text", "new_text"],
        )

    async def execute(self, path: str, old_text: str, new_text: str, replace_all: bool = False, **_: Any) -> str:
        try:
            target = self._resolve(path)
            if path.endswith(".ipynb"):
                return "Error: This is a Jupyter notebook. Use notebook_edit instead of edit_file."
            if not target.exists():
                if old_text == "":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(new_text, encoding="utf-8")
                    return f"Successfully created {target}"
                return _file_not_found_msg(path, target)
            size = target.stat().st_size
            if size > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({size / (1024**3):.1f} GiB). Maximum is 1 GiB."
            raw = target.read_bytes()
            uses_crlf = b"\r\n" in raw
            text = raw.decode("utf-8").replace("\r\n", "\n")
            if old_text == "":
                if text.strip():
                    return f"Error: Cannot create file - {path} already exists and is not empty."
                target.write_text(new_text, encoding="utf-8")
                return f"Successfully edited {target}"
            normalized_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(text, normalized_old)
            if not matches:
                return _not_found_msg(normalized_old, text, path)
            if len(matches) > 1 and not replace_all:
                preview = ", ".join(f"line {match.line}" for match in matches[:3])
                if len(matches) > 3:
                    preview += ", ..."
                return (
                    f"Warning: old_text appears {len(matches)} times at {preview}. "
                    "Provide more context to make it unique, or set replace_all=true."
                )
            normalized_new = new_text.replace("\r\n", "\n")
            if target.suffix.lower() not in self._MARKDOWN_EXTS:
                normalized_new = "\n".join(line.rstrip() for line in normalized_new.split("\n"))
            updated = text
            for match in reversed(matches if replace_all else matches[:1]):
                replacement = _preserve_quote_style(normalized_old, match.text, normalized_new)
                replacement = _reindent_like_match(normalized_old, match.text, replacement)
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and updated[end : end + 1] == "\n":
                    end += 1
                updated = updated[: match.start] + replacement + updated[end:]
            if uses_crlf:
                updated = updated.replace("\n", "\r\n")
            target.write_bytes(updated.encode("utf-8"))
            return f"Successfully edited {target} ({len(matches) if replace_all else 1} replacement(s))"
        except Exception as exc:
            return f"Error editing file: {exc}"


class ListDirTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and directories inside the workspace."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "path": {"type": "string", "description": "Directory to list, default '.'"},
                "recursive": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000},
            }
        )

    async def execute(self, path: str = ".", recursive: bool = False, limit: int | None = None, max_entries: int | None = None, **_: Any) -> str:
        try:
            root = self._resolve(path)
            if not root.exists():
                return f"Error: Path not found: {path}"
            if root.is_file():
                return f"{root.name}"
            cap = max_entries or limit or 200
            entries: list[str] = []
            if recursive:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS)
                    current = Path(dirpath)
                    for name in sorted(dirnames) + sorted(filenames):
                        rel = (current / name).relative_to(root).as_posix()
                        entries.append(rel + ("/" if (current / name).is_dir() else ""))
                        if len(entries) >= cap:
                            return "\n".join(entries) + "\n... (truncated)"
            else:
                for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if child.name in _IGNORE_DIRS:
                        continue
                    entries.append(child.name + ("/" if child.is_dir() else ""))
                    if len(entries) >= cap:
                        return "\n".join(entries) + "\n... (truncated)"
            return "\n".join(entries) if entries else "(empty directory)"
        except Exception as exc:
            return f"Error listing directory: {exc}"


class NotebookEditTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "notebook_edit"

    @property
    def description(self) -> str:
        return "Edit Jupyter notebook cells: replace, insert, or delete."

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "cell_index": {"type": "integer", "minimum": 0},
                "new_source": {"type": "string"},
                "cell_type": {"type": "string", "enum": ["code", "markdown"]},
                "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
            },
            required=["path", "cell_index"],
        )

    async def execute(
        self,
        path: str,
        cell_index: int,
        new_source: str = "",
        cell_type: str = "code",
        edit_mode: str = "replace",
        **_: Any,
    ) -> str:
        if not path.endswith(".ipynb"):
            return "Error: notebook_edit only works on .ipynb files"
        try:
            target = self._resolve(path)
            nb = _read_notebook(target) if target.exists() else _empty_notebook()
            cells = nb.setdefault("cells", [])
            if edit_mode == "delete":
                if cell_index >= len(cells):
                    return f"Error: cell_index {cell_index} out of range"
                cells.pop(cell_index)
            elif edit_mode == "insert":
                cells.insert(min(cell_index + 1, len(cells)), _new_cell(new_source, cell_type))
            else:
                if cell_index >= len(cells):
                    return f"Error: cell_index {cell_index} out of range"
                cells[cell_index] = _new_cell(new_source, cell_type)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
            return f"Successfully {edit_mode}ed notebook cell in {target}"
        except Exception as exc:
            return f"Error editing notebook: {exc}"


