"""Small, explicit budgets for conversation and persistent runtime data."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# shortcut: budgets use UTF-8 bytes, use the provider tokenizer when exact token limits are required.
RECENT_CONTEXT_BYTES = 24_000
SUMMARY_BYTES = 4_000
RECALL_BYTES = 4_000
DREAM_INPUT_BYTES = 48_000
MAX_MESSAGE_BYTES = 16_000
MAX_MEMORIES = 3_000
STORAGE_BYTES = 128 * 1024 * 1024


class StorageLimitError(RuntimeError):
    """The configured runtime storage budget cannot safely accept more data."""


def clip_text(text: str, limit: int) -> str:
    """Bound UTF-8 bytes without splitting a character."""
    return text.encode("utf-8")[:max(0, limit)].decode("utf-8", "ignore")


def compact_payload(value: Any, *, depth: int = 0) -> Any:
    """Keep useful audit data without persisting binary bodies or unbounded dumps."""
    if depth > 8:
        return "[nested payload omitted]"
    if isinstance(value, str):
        if value.startswith("data:") or len(value) > 4096 and not any(c.isspace() for c in value):
            return "[binary payload omitted]"
        return clip_text(value, 4096)
    if isinstance(value, dict):
        return {str(k): compact_payload(v, depth=depth + 1) for k, v in list(value.items())[:64]}
    if isinstance(value, list):
        return [compact_payload(item, depth=depth + 1) for item in value[:100]]
    return value


def runtime_size(data_dir: Path) -> int:
    files = list(data_dir.glob("orison.sqlite3*"))
    for directory in ("dream", "outbox", "reminders_outbox", "media"):
        files.extend((data_dir / directory).rglob("*"))
    size = 0
    for path in files:
        try:
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
        except FileNotFoundError:
            continue
    return size


def ensure_storage_capacity(data_dir: Path, incoming: int = 0) -> None:
    if runtime_size(data_dir) + incoming > STORAGE_BYTES:
        raise StorageLimitError("Runtime storage budget exceeded; pending conversations have been preserved.")
