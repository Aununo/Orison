"""Reserved provider shells for post-MVP API providers.

These classes make future provider names explicit without putting private API
auth, streaming, or tool-call formats into Core. They intentionally report
unavailable until a concrete provider module implements the common Provider
boundary.
"""

from __future__ import annotations

from typing import AsyncIterator

from orison.config import ProviderConfig
from orison.providers.base import ProviderEvent, ProviderHealth, ProviderRequest, ProviderRunResult


class ReservedAPIProvider:
    """Provider placeholder that preserves the Core contract for future APIs."""

    name = "reserved"

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        return ProviderRunResult(content="", error=self._unavailable_message())

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        return ProviderRunResult(
            content="",
            provider_session_id=provider_session_id,
            error=self._unavailable_message(),
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        message = self._unavailable_message()
        yield ProviderEvent(type="error", content=message, error=message)

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=False, message=self._unavailable_message())

    def _unavailable_message(self) -> str:
        return (
            f"Provider {self.name!r} is reserved for a future API implementation. "
            "Add provider-specific auth, event parsing, tool-call conversion, usage extraction, "
            "and error normalization inside its provider module before enabling it."
        )
