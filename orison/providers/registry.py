"""Provider registry for Orison Core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from orison.config import OrisonConfig, ProviderConfig
from orison.providers.base import Provider
from orison.providers.deepseek import DeepSeekProvider
from orison.providers.kimi import KimiProvider
from orison.providers.local_openai_compatible import LocalOpenAICompatibleProvider
from orison.providers.openai import OpenAIProvider
from orison.providers.openai_codex import OpenAICodexProvider


ProviderFactory = Callable[[ProviderConfig], Provider]


PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "openai-codex": OpenAICodexProvider,
    "openai": OpenAIProvider,
    "deepseek": DeepSeekProvider,
    "kimi": KimiProvider,
    "local-openai-compatible": LocalOpenAICompatibleProvider,
}


@dataclass(frozen=True)
class ProviderRegistry:
    default_name: str
    providers: dict[str, Provider]

    def default(self) -> Provider:
        if self.default_name not in self.providers:
            raise KeyError(f"Default provider is not registered: {self.default_name}")
        return self.providers[self.default_name]

    def get(self, name: str | None = None) -> Provider:
        provider_name = name or self.default_name
        if provider_name not in self.providers:
            raise KeyError(f"Provider is not registered: {provider_name}")
        return self.providers[provider_name]

    def names(self) -> list[str]:
        return sorted(self.providers)


def build_provider_registry(config: OrisonConfig) -> ProviderRegistry:
    providers: dict[str, Provider] = {}
    for name, provider_config in config.providers.items():
        factory = PROVIDER_FACTORIES.get(name)
        if factory is None:
            raise ValueError(f"Unsupported provider: {name}")
        providers[name] = factory(provider_config)
    return ProviderRegistry(default_name=config.default_provider, providers=providers)
