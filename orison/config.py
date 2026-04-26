"""Configuration loading for Orison."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    reasoning_effort: str
    sandbox: str
    workdir: str
    full_auto: bool = False
    extra_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrisonConfig:
    data_dir: str
    default_provider: str
    providers: dict[str, ProviderConfig]


def load_config(path: str | Path = "config.toml") -> OrisonConfig:
    config_path = Path(path)
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required to load TOML config with tomllib")
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    provider_section = _require_dict(raw, "provider")
    default_provider = _require_str(provider_section, "default")

    providers_raw = _require_dict(raw, "providers")
    providers: dict[str, ProviderConfig] = {}
    for name, provider_raw in providers_raw.items():
        if not isinstance(provider_raw, dict):
            raise ValueError(f"providers.{name} must be a table")
        providers[name] = ProviderConfig(
            name=name,
            model=_require_str(provider_raw, "model"),
            reasoning_effort=_require_str(provider_raw, "reasoning_effort"),
            sandbox=_require_str(provider_raw, "sandbox"),
            workdir=_require_str(provider_raw, "workdir"),
            full_auto=_optional_bool(provider_raw.get("full_auto", False), f"providers.{name}.full_auto"),
            extra_args=_string_list(provider_raw.get("extra_args", []), f"providers.{name}.extra_args"),
        )

    if default_provider not in providers:
        raise ValueError(f"provider.default {default_provider!r} is not configured")

    orison_section = _require_dict(raw, "orison")
    return OrisonConfig(
        data_dir=_require_str(orison_section, "data_dir"),
        default_provider=default_provider,
        providers=providers,
    )


def _require_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a TOML table")
    return value


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _string_list(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


def _optional_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
