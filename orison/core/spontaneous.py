"""Occasional unprompted replies in the family group, gated before any model call."""

from __future__ import annotations

import random
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from orison.config import SpontaneousConfig
from orison.timezone import BOT_TIMEZONE


# Recorded as the speaker and message of a spontaneous turn, so history shows why the reply happened.
SPONTANEOUS_USER_ID = "orison:spontaneous"
SPONTANEOUS_MESSAGE = "(No one addressed you. The family conversation has paused.)"
SILENT_REPLY = "[silent]"
SPONTANEOUS_GUIDANCE = f"""No one addressed you. The family has been chatting in the group and the conversation has paused.
You may join in if you have something natural and relevant to add, as a family member chiming in.
Keep it short. Do not repeat what was already said, mention that you were watching, or ask permission to join.
If staying quiet is more natural, reply with exactly {SILENT_REPLY}."""
# Side-effecting tools stay unavailable: nobody asked for an action.
SPONTANEOUS_EXCLUDED_TOOLS = ("message", "reminder", "cron", "todo")

_SILENT_PATTERN = re.compile(r"\[?\s*silent\s*\]?[.。]?", re.IGNORECASE)


def is_silent(reply: str) -> bool:
    return not reply.strip() or bool(_SILENT_PATTERN.fullmatch(reply.strip()))


class SpontaneousGate:
    """Tracks family group pauses and applies the cheap checks before a spontaneous turn."""

    def __init__(self, config: SpontaneousConfig, *, random_fn: Callable[[], float] = random.random) -> None:
        self.config = config
        self.random = random_fn
        self._lock = threading.Lock()
        self._last_observed: datetime | None = None

    def record(self, *, addressed: bool, at: datetime) -> None:
        with self._lock:
            # A requested reply already joins the conversation, so it ends the pending pause.
            self._last_observed = None if addressed else at

    def take_due(self, now: datetime) -> bool:
        """Return True once per pause that has lasted at least settle_seconds."""
        with self._lock:
            if self._last_observed is None or now - self._last_observed < timedelta(seconds=self.config.settle_seconds):
                return False
            self._last_observed = None
            return True

    def skip_reason(self, now: datetime, *, last_reply_at: datetime | None, replies_today: int) -> str | None:
        """Return why this pause stays silent, or None when the model may decide."""
        quiet = self.config.quiet_window()
        if quiet is not None:
            start, end = quiet
            local = now.astimezone(BOT_TIMEZONE).time()
            if (start <= local < end) if start < end else (local >= start or local < end):
                return "quiet hours"
        if last_reply_at is not None and now - last_reply_at < timedelta(minutes=self.config.cooldown_minutes):
            return "cooldown"
        if replies_today >= self.config.daily_max:
            return "daily limit"
        if self.random() >= self.config.probability:
            return "probability"
        return None
