"""One primary-to-backup switch per turn, preserving context and completed tools."""

from __future__ import annotations

import re
from contextlib import aclosing
from dataclasses import asdict, replace
from typing import AsyncIterator

from orison.providers.base import Provider, ProviderEvent, ProviderHealth, ProviderRequest, ProviderRunResult


class FailoverProvider:
    def __init__(self, primary: Provider, backup: Provider) -> None:
        self.primary, self.backup, self.active = primary, backup, primary
        self.config = getattr(primary, "config", None)
        self.pending: list[ProviderEvent] = []

    @property
    def name(self) -> str:
        return self.active.name

    def healthcheck(self) -> ProviderHealth:
        return self.primary.healthcheck()

    def _switch(self, error: str) -> None:
        self.active = self.backup
        self.pending.append(ProviderEvent(
            type="status",
            content=f"Primary model {self.primary.name} is unavailable: {_safe_reason(error)} Switching to {self.backup.name}.",
            metadata={"provider_fallback": True, "from_provider": self.primary.name, "to_provider": self.backup.name},
        ))

    def _request(self, request: ProviderRequest) -> ProviderRequest:
        metadata = dict(request.metadata)
        marker = str(metadata.pop("provider_session_id", "") or "")
        prefix = self.backup.name + ":"
        if marker and self.active is self.primary and not marker.startswith(prefix):
            metadata["provider_session_id"] = marker
        elif marker.startswith(prefix) and self.active is self.backup:
            metadata["provider_session_id"] = marker[len(prefix):]
        metadata["provider"] = self.active.name
        system_context = request.system_context
        if self.active is self.backup:
            system_context += f"\n\nActive model provider: {self.backup.name}. The preferred provider in earlier context is unavailable."
        return replace(request, metadata=metadata, system_context=system_context,
                       model=None if self.active is self.backup else request.model)

    def _marker(self, marker: str | None) -> str | None:
        if marker and self.active is self.backup:
            return self.backup.name + ":" + marker
        return marker

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        return self._run(None, request)

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        return self._run(provider_session_id, request)

    def _run(self, marker: str | None, request: ProviderRequest) -> ProviderRunResult:
        while True:
            try:
                prepared = self._request(request)
                prefix = self.backup.name + ":"
                if marker and self.active is self.primary and not marker.startswith(prefix):
                    result = self.active.resume_session(marker, prepared)
                else:
                    result = self.active.start_session(prepared)
            except Exception as exc:
                result = ProviderRunResult(content="", error=_safe_reason(str(exc)))
            if result.error and self.active is self.primary:
                self._switch(result.error)
                continue
            notices = []
            if not request.metadata.get("orison_internal"):
                notices = [asdict(event) for event in self.pending]
                self.pending.clear()
            return replace(result, provider_session_id=self._marker(result.provider_session_id),
                           raw_events=notices + result.raw_events)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        while True:
            for event in self.pending:
                yield event
            self.pending.clear()
            failed = None
            completed = False
            try:
                async with aclosing(self.active.stream(self._request(request))) as stream:
                    async for event in stream:
                        if event.type == "error":
                            failed = event.error or event.content or "Provider stream failed"
                            break
                        completed = completed or event.type == "final"
                        yield replace(event, provider_session_id=self._marker(event.provider_session_id))
            except Exception as exc:
                failed = _safe_reason(str(exc))
            if not failed and not completed:
                failed = "Provider stream ended before completion"
            if failed and self.active is self.primary:
                self._switch(failed)
                continue
            if failed:
                yield ProviderEvent(type="error", content=failed, error=failed)
            return


def _safe_reason(error: str) -> str:
    """Classify failures without exposing response bodies, tokens, or local paths."""
    text = error.lower()
    if "not installed" in text or "no module named" in text:
        return "The provider authentication dependency is not installed."
    if any(word in text for word in ("oauth", "login", "auth", "credential", "401", "403")):
        return "Authorization is missing, expired, or denied; sign in again."
    if any(word in text for word in ("429", "rate limit", "quota")):
        return "The provider rate limit or quota was exceeded."
    if any(word in text for word in ("timeout", "timed out")):
        return "The provider request timed out."
    status = re.search(r"http\s+(\d{3})", text)
    if status:
        return "The provider returned HTTP " + status[1] + "."
    if any(word in text for word in ("connection", "network", "connect")):
        return "The provider connection failed."
    if "before completion" in text or "incomplete" in text:
        return "The provider stream ended before completion."
    return "The provider request failed."
