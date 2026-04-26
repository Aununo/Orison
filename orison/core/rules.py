"""Deterministic built-in reply rules for Orison Core."""

from __future__ import annotations


def resolve_builtin_response(message: str) -> str | None:
    """Resolve a deterministic built-in reply, or None when no rule matches."""
    text = (message or "").strip()
    if text == "/ping":
        return "pong!"
    return None
