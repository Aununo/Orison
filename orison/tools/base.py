"""Small function-tool registry for Orison."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any


_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        ...

    @property
    def read_only(self) -> bool:
        return False

    @property
    def exclusive(self) -> bool:
        return False

    @property
    def concurrency_safe(self) -> bool:
        return self.read_only and not self.exclusive

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        ...

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        schema = self.parameters
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        casted: dict[str, Any] = {}
        for key, value in params.items():
            prop_schema = props.get(key, {})
            casted[key] = _cast_value(value, prop_schema)
        return casted

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters
        errors: list[str] = []
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in params:
                errors.append(f"missing required {key}")
        for key, value in params.items():
            prop_schema = props.get(key)
            if isinstance(prop_schema, dict):
                errors.extend(_validate_value(key, value, prop_schema))
        return errors


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        if self._cached_definitions is None:
            self._cached_definitions = sorted(
                (tool.to_schema() for tool in self._tools.values()),
                key=lambda schema: str((schema.get("function") or {}).get("name") or ""),
            )
        return deepcopy(self._cached_definitions)

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        if not isinstance(params, dict):
            return None, params, f"Error: Tool '{name}' parameters must be a JSON object"
        tool = self.get(name)
        if tool is None:
            return None, params, f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, f"Error: Invalid parameters for tool '{name}': {'; '.join(errors)}"
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, cast_params, error = self.prepare_call(name, params)
        if error:
            return error + hint
        try:
            assert tool is not None
            result = await tool.execute(**cast_params)
        except Exception as exc:
            return f"Error executing {name}: {exc}" + hint
        if isinstance(result, str) and result.startswith("Error"):
            return result + hint
        return result

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def object_schema(
    properties: dict[str, dict[str, Any]],
    *,
    required: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }
    if description:
        schema["description"] = description
    return schema


def _schema_type(schema: dict[str, Any]) -> str | None:
    raw = schema.get("type")
    if isinstance(raw, list):
        return next((item for item in raw if item != "null"), None)
    return raw if isinstance(raw, str) else None


def _cast_value(value: Any, schema: dict[str, Any]) -> Any:
    kind = _schema_type(schema)
    if kind == "string":
        return value if value is None else str(value)
    if kind == "integer" and isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    if kind == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if kind == "boolean" and isinstance(value, str):
        low = value.lower()
        if low in {"true", "1", "yes"}:
            return True
        if low in {"false", "0", "no"}:
            return False
    if kind == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [_cast_value(item, item_schema) for item in value]
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        if isinstance(props, dict):
            return {
                key: _cast_value(item, props[key]) if isinstance(props.get(key), dict) else item
                for key, item in value.items()
            }
    return value


def _validate_value(label: str, value: Any, schema: dict[str, Any]) -> list[str]:
    kind = _schema_type(schema)
    errors: list[str] = []
    if value is None and (schema.get("nullable") or (isinstance(schema.get("type"), list) and "null" in schema["type"])):
        return errors
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        errors.append(f"{label} should be integer")
        return errors
    if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        errors.append(f"{label} should be number")
        return errors
    if kind in _TYPE_MAP and kind not in {"integer", "number"} and not isinstance(value, _TYPE_MAP[kind]):
        errors.append(f"{label} should be {kind}")
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{label} must be one of {schema['enum']}")
    if kind in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{label} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{label} must be <= {schema['maximum']}")
    if kind == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{label} must be at least {schema['minLength']} chars")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{label} must be at most {schema['maxLength']} chars")
    if kind == "object":
        props = schema.get("properties", {})
        if isinstance(props, dict):
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"missing required {label}.{key}")
            for key, item in value.items():
                prop_schema = props.get(key)
                if isinstance(prop_schema, dict):
                    errors.extend(_validate_value(f"{label}.{key}", item, prop_schema))
    if kind == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{label} must have at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{label} must be at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                errors.extend(_validate_value(f"{label}[{idx}]", item, item_schema))
    return errors
