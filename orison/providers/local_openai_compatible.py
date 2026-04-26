"""Reserved local OpenAI-compatible API provider."""

from __future__ import annotations

from orison.providers.reserved import ReservedAPIProvider


class LocalOpenAICompatibleProvider(ReservedAPIProvider):
    name = "local-openai-compatible"
