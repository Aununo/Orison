from __future__ import annotations

import asyncio
import json
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import patch

from orison.config import OrisonConfig, ProviderConfig, load_config
from orison.providers import (
    Provider,
    ProviderEvent,
    ProviderMessage,
    ProviderRequest,
    ProviderRunResult,
)
from orison.providers.registry import build_provider_registry


class ProviderInterfaceTest(unittest.TestCase):
    def test_registry_builds_default_provider_from_config(self) -> None:
        config = load_config("config.toml")
        registry = build_provider_registry(config)

        self.assertEqual(registry.default_name, "openai-codex")
        self.assertEqual(registry.names(), ["openai-codex"])
        self.assertIs(registry.default(), registry.get())
        self.assertIsInstance(registry.default(), Provider)

    def test_registry_can_build_reserved_future_providers_without_core_changes(self) -> None:
        provider_config = ProviderConfig(
            name="openai",
            model="gpt-5.5",
            reasoning_effort="medium",
            sandbox="workspace-write",
            workdir=".",
        )
        config = OrisonConfig(
            data_dir=".orison",
            default_provider="openai",
            providers={"openai": provider_config},
        )

        registry = build_provider_registry(config)
        provider = registry.default()
        result = provider.start_session(ProviderRequest(messages=[ProviderMessage(role="user", content="hello")]))
        health = provider.healthcheck()

        self.assertEqual(registry.names(), ["openai"])
        self.assertIn("reserved for a future API implementation", result.error or "")
        self.assertFalse(health.ok)

    def test_openai_codex_uses_oauth_responses_api_not_codex_cli(self) -> None:
        config = load_config("config.toml")
        provider = build_provider_registry(config).default()
        request = ProviderRequest(
            messages=[ProviderMessage(role="user", content="hello")],
            system_context="You are Orison.",
            session_id="orison-session-1",
            metadata={"channel": "test"},
        )

        raw_events = [
            {"type": "response.output_text.delta", "delta": "hello "},
            {"type": "response.output_text.delta", "delta": "back"},
            {"type": "response.completed", "response": {"id": "resp_1", "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}}},
        ]

        captured: dict[str, object] = {}

        async def fake_request(url, headers, body, on_event=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = body
            if on_event:
                for event in raw_events:
                    await on_event(event)
            return "hello back", raw_events, {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}, "resp_1", None, []

        token = SimpleNamespace(account_id="acct_123", access="access-token")
        with patch("orison.providers.openai_codex.get_codex_token", return_value=token), patch(
            "orison.providers.openai_codex._request_codex", side_effect=fake_request
        ), patch("orison.providers.openai_codex.subprocess.run") as run, patch(
            "orison.providers.openai_codex.subprocess.Popen"
        ) as popen:
            result = provider.start_session(request)
            resumed = provider.resume_session("resp_1", request)

        self.assertIsInstance(result, ProviderRunResult)
        self.assertIsNone(result.error)
        self.assertEqual(result.content, "hello back")
        self.assertEqual(result.provider_session_id, "resp_1")
        self.assertEqual(result.usage, {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
        self.assertEqual(resumed.provider_session_id, "resp_1")
        self.assertIsNone(resumed.error)
        self.assertFalse(run.called)
        self.assertFalse(popen.called)

        self.assertEqual(captured["url"], "https://chatgpt.com/backend-api/codex/responses")
        headers = captured["headers"]
        self.assertEqual(headers["Authorization"], "Bearer access-token")
        self.assertEqual(headers["chatgpt-account-id"], "acct_123")
        self.assertEqual(headers["originator"], "orison")
        body = captured["body"]
        self.assertEqual(body["model"], "gpt-5.5")
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertEqual(body["instructions"], "You are Orison.")
        self.assertEqual(body["input"][0]["role"], "user")
        self.assertEqual(body["input"][0]["content"][0]["text"], "hello")
        self.assertIn({"type": "image_generation"}, body["tools"])
        self.assertEqual(body["reasoning"], {"effort": config.providers["openai-codex"].reasoning_effort})

    def test_openai_codex_converts_channel_images_to_multimodal_input(self) -> None:
        config = load_config("config.toml")
        provider = build_provider_registry(config).default()
        request = ProviderRequest(
            messages=[
                ProviderMessage(
                    role="user",
                    content="这张图里有什么？",
                    metadata={"images": [{"url": "https://example.test/cat.png"}]},
                )
            ]
        )

        body = provider._build_body(request)  # type: ignore[attr-defined]

        content = body["input"][0]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "这张图里有什么？"})
        self.assertEqual(content[1], {"type": "input_image", "image_url": "https://example.test/cat.png"})

    def test_openai_codex_ignores_audio_metadata_after_audio_modality_removed(self) -> None:
        config = load_config("config.toml")
        provider = build_provider_registry(config).default()
        request = ProviderRequest(
            messages=[
                ProviderMessage(
                    role="user",
                    content="这是一条普通消息",
                    metadata={"audios": [{"data": "UklGRg==", "format": "mp3"}]},
                )
            ]
        )

        body = provider._build_body(request)  # type: ignore[attr-defined]

        content = body["input"][0]["content"]
        self.assertEqual(content, [{"type": "input_text", "text": "这是一条普通消息"}])

    def test_openai_codex_extracts_generated_images_from_responses_events(self) -> None:
        from orison.providers.openai_codex import _extract_image_artifacts

        artifacts = _extract_image_artifacts(
            [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "result": "iVBORw0KGgo=",
                    },
                }
            ]
        )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["mime_type"], "image/png")
        self.assertTrue(artifacts[0]["data"].startswith("data:image/png;base64,"))

    def test_openai_codex_stream_uses_responses_sse_events(self) -> None:
        async def collect() -> list[ProviderEvent]:
            config = load_config("config.toml")
            provider = build_provider_registry(config).default()
            request = ProviderRequest(
                messages=[ProviderMessage(role="user", content="hello")],
                system_context="You are Orison.",
            )
            return [event async for event in provider.stream(request)]

        async def fake_request(url, headers, body, on_event=None):
            for event in [
                {"type": "response.created", "response": {"id": "resp_stream"}},
                {"type": "response.output_text.delta", "delta": "streamed"},
                {"type": "response.completed", "response": {"id": "resp_stream"}},
            ]:
                if on_event:
                    await on_event(event)
            return "streamed", [], {}, "resp_stream", None, []

        token = SimpleNamespace(account_id="acct_123", access="access-token")
        with patch("orison.providers.openai_codex.get_codex_token", return_value=token), patch(
            "orison.providers.openai_codex._request_codex", side_effect=fake_request
        ), patch("orison.providers.openai_codex.subprocess.run") as run, patch(
            "orison.providers.openai_codex.subprocess.Popen"
        ) as popen:
            events = asyncio.run(collect())

        self.assertEqual([event.type for event in events], ["session_started", "text_delta", "final"])
        self.assertTrue(all(isinstance(event, ProviderEvent) for event in events))
        self.assertEqual(events[0].provider_session_id, "resp_stream")
        self.assertEqual(events[1].content, "streamed")
        self.assertEqual(events[2].content, "streamed")
        self.assertFalse(run.called)
        self.assertFalse(popen.called)

    def test_openai_codex_reports_missing_oauth_login(self) -> None:
        config = load_config("config.toml")
        provider = build_provider_registry(config).default()
        request = ProviderRequest(messages=[ProviderMessage(role="user", content="hello")])

        with patch(
            "orison.providers.openai_codex.get_codex_token",
            side_effect=RuntimeError("OAuth credentials not found. Please run the login command."),
        ), patch("orison.providers.openai_codex.subprocess.run") as run:
            missing = provider.start_session(request)

        self.assertIn("Codex OAuth authentication failed", missing.error or "")
        self.assertIn("nanobot provider login openai-codex", missing.error or "")
        self.assertFalse(run.called)
    def test_openai_codex_sync_call_inside_loop_closes_unawaited_coroutine(self) -> None:
        async def call_inside_loop():
            config = load_config("config.toml")
            provider = build_provider_registry(config).default()
            request = ProviderRequest(messages=[ProviderMessage(role="user", content="hello")])
            return provider.start_session(request)

        async def fake_request(url, headers, body, on_event=None):
            return "", [], {}, None, None, []

        token = SimpleNamespace(account_id="acct_123", access="access-token")
        with patch("orison.providers.openai_codex.get_codex_token", return_value=token), patch(
            "orison.providers.openai_codex._request_codex", side_effect=fake_request
        ), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = asyncio.run(call_inside_loop())

        self.assertIn("active event loop", result.error or "")
        self.assertFalse(any("was never awaited" in str(warning.message) for warning in caught))


if __name__ == "__main__":
    unittest.main()

