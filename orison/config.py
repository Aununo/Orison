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
    service_tier: str | None = None


@dataclass(frozen=True)
class OrisonConfig:
    data_dir: str
    default_provider: str
    providers: dict[str, ProviderConfig]
    family: dict[str, Any] = field(default_factory=dict)
    fallback_provider: str | None = None


def load_config(path: str | Path = "config.toml") -> OrisonConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Private configuration file is missing: {config_path}")
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required to load TOML config with tomllib")
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    provider_section = _require_dict(raw, "provider")
    default_provider = _require_str(provider_section, "default")
    fallback_provider = provider_section.get("fallback")
    if fallback_provider is not None and (not isinstance(fallback_provider, str) or not fallback_provider):
        raise ValueError("provider.fallback must be a non-empty provider name")

    providers_raw = _require_dict(raw, "providers")
    providers: dict[str, ProviderConfig] = {}
    for name, provider_raw in providers_raw.items():
        if not isinstance(provider_raw, dict):
            raise ValueError(f"providers.{name} must be a table")
        if name == "deepseek":
            provider_raw = {"reasoning_effort": "none", "sandbox": "read-only", "workdir": ".", **provider_raw}
        service_tier = provider_raw.get("service_tier")
        if service_tier not in (None, "auto", "default", "fast", "priority"):
            raise ValueError(f"providers.{name}.service_tier must be auto, default, fast, or priority")
        providers[name] = ProviderConfig(
            name=name,
            model=_require_str(provider_raw, "model"),
            reasoning_effort=_require_str(provider_raw, "reasoning_effort"),
            sandbox=_require_str(provider_raw, "sandbox"),
            workdir=_require_str(provider_raw, "workdir"),
            full_auto=_optional_bool(provider_raw.get("full_auto", False), f"providers.{name}.full_auto"),
            extra_args=_string_list(provider_raw.get("extra_args", []), f"providers.{name}.extra_args"),
            service_tier=service_tier,
        )

    if default_provider not in providers:
        raise ValueError(f"provider.default {default_provider!r} is not configured")
    if fallback_provider is not None and (fallback_provider not in providers or fallback_provider == default_provider):
        raise ValueError("provider.fallback must name a configured provider different from the primary")

    orison_section = _require_dict(raw, "orison")
    return OrisonConfig(
        data_dir=_require_str(orison_section, "data_dir"),
        default_provider=default_provider,
        providers=providers,
        family=_load_family(raw.get("family", {})),
        fallback_provider=fallback_provider,
    )


def _load_family(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("family must be a TOML table")
    group_id = value.get("group_id", "")
    if not isinstance(group_id, str) or len(group_id) > 20 or (group_id and not group_id.isascii()) or (group_id and not group_id.isdigit()):
        raise ValueError("family.group_id must be a QQ group ID string")
    members = {}
    for role in ("father", "mother"):
        member = value.get(role, {})
        if not isinstance(member, dict):
            raise ValueError(f"family.{role} must be a TOML table")
        user_id = member.get("user_id", "")
        if not isinstance(user_id, str) or len(user_id) > 20 or (user_id and (not user_id.isascii() or not user_id.isdigit())):
            raise ValueError(f"family.{role}.user_id must be a QQ user ID string")
        if user_id:
            if user_id in members:
                raise ValueError("Family members must have distinct QQ user IDs")
            members[user_id] = {
                "role": role,
                "name": _require_str(member, "name"),
                "native_name": member.get("native_name", ""),
            }
            if not isinstance(members[user_id]["native_name"], str):
                raise ValueError(f"family.{role}.native_name must be a string")
            if len(members[user_id]["name"]) > 80 or len(members[user_id]["native_name"]) > 80:
                raise ValueError(f"family.{role} names must not exceed 80 characters")
    return {"group_id": group_id, "members": members}


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
