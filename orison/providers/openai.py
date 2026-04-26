"""Reserved OpenAI API provider.

MVP support is intentionally limited to openai-codex. A future OpenAI API
provider should implement auth, streaming, tool calls, usage, and error
normalization here while keeping Core on the common Provider interface.
"""

from __future__ import annotations

from orison.providers.reserved import ReservedAPIProvider


class OpenAIProvider(ReservedAPIProvider):
    name = "openai"
