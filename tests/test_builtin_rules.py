from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from orison.core import SessionManager
from orison.core.rules import resolve_builtin_response
from orison.providers import ProviderHealth, ProviderRegistry, ProviderRequest, ProviderRunResult


class RecordingProvider:
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        self.requests.append(request)
        return ProviderRunResult(content="provider reply")

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        self.requests.append(request)
        return ProviderRunResult(content="provider reply", provider_session_id=provider_session_id)

    async def stream(self, request: ProviderRequest):
        self.requests.append(request)
        return
        yield

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=True, message="ok")


class BuiltinRulesTest(unittest.TestCase):
    def test_ping_rule_replies_pong(self) -> None:
        self.assertEqual(resolve_builtin_response("/ping"), "pong!")
        self.assertEqual(resolve_builtin_response(" /ping "), "pong!")
        self.assertIsNone(resolve_builtin_response("BV1Q541167Qg"))

    def test_session_manager_streams_builtin_ping_rule_without_provider(self) -> None:
        provider = RecordingProvider()
        registry = ProviderRegistry(default_name="fake", providers={"fake": provider})

        async def collect_events(manager: SessionManager):
            return [
                event
                async for event in manager.stream_message(
                    "qq:g:1",
                    "/ping",
                    channel="qq",
                    user_id="123",
                )
            ]

        with tempfile.TemporaryDirectory() as tempdir:
            manager = SessionManager(data_dir=Path(tempdir) / "data", registry=registry)
            events = asyncio.run(collect_events(manager))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "final")
        self.assertEqual(events[0].content, "pong!")
        self.assertEqual(provider.requests, [])


if __name__ == "__main__":
    unittest.main()
