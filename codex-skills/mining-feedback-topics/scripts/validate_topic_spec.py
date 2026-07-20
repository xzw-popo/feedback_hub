#!/usr/bin/env python3
"""Validate a feedback-topic specification against the bundled schema."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


_RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
_RFC3339_LOCAL_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?$")


def _error(path: str, message: str) -> ValueError:
    return ValueError(f"{path}: {message}")


def _load_input(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise _error("$", f"cannot read topic spec: {error.strerror or 'I/O error'}") from error
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise _error("$", "invalid topic spec syntax") from error
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as error:
            raise _error("$", "PyYAML is required for YAML topic specs") from error
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise _error("$", "invalid topic spec syntax") from error
    raise _error("$", "topic spec path must end in .json, .yaml, or .yml")


def _load_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "references" / "topic-spec.schema.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _error("$", "bundled topic schema is invalid") from error
    if not isinstance(value, dict):
        raise _error("$", "bundled topic schema is invalid")
    return value


def _is_type(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    return False


def _json_value_matches(value: Any, expected: Any) -> bool:
    if isinstance(value, bool) or isinstance(expected, bool):
        return isinstance(value, bool) and isinstance(expected, bool) and value is expected
    return value == expected


def _json_const_matches(value: Any, expected: Any) -> bool:
    """Match JSON Schema constants without Python's bool/int equality leak."""
    return _json_value_matches(value, expected)


def _parse_rfc3339(value: str, path: str) -> datetime:
    if not _RFC3339_DATETIME.fullmatch(value):
        if _RFC3339_LOCAL_DATETIME.fullmatch(value):
            raise _error(path, "timezone is required")
        raise _error(path, "must be an RFC3339 datetime")
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise _error(path, "must be an RFC3339 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _error(path, "timezone is required")
    return parsed


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    if "const" in schema and not _json_const_matches(value, schema["const"]):
        raise _error(path, f"must equal {schema['const']!r}")
    if "enum" in schema and not any(_json_value_matches(value, option) for option in schema["enum"]):
        choices = ", ".join(str(item) for item in schema["enum"])
        raise _error(path, f"must be one of: {choices}")

    type_name = schema.get("type")
    if isinstance(type_name, str) and not _is_type(value, type_name):
        article = "an" if type_name in {"array", "object"} else "a"
        raise _error(path, f"must be {article} {type_name}")

    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            raise _error(path, "must be a non-empty string")
        if schema.get("format") == "date-time":
            _parse_rfc3339(value, path)

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise _error(path, f"missing required field: {key}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    raise _error(path, f"unknown field: {key}")
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                _validate(value[key], child, f"{path}.{key}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise _error(path, f"must contain at least {schema['minItems']} items")
        if len(value) > schema.get("maxItems", float("inf")):
            raise _error(path, f"must contain at most {schema['maxItems']} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, child in enumerate(value):
                _validate(child, items, f"{path}[{index}]")
        for child_schema in schema.get("allOf", []):
            if isinstance(child_schema, dict):
                _validate(value, child_schema, path)
        if "contains" in schema:
            contains = schema["contains"]
            if not isinstance(contains, dict) or not any(_matches(item, contains) for item in value):
                raise _error(path, "must contain a required value")


def _matches(value: Any, schema: dict[str, Any]) -> bool:
    try:
        _validate(value, schema, "$")
    except ValueError:
        return False
    return True


def _validate_scope_time_order(value: Any) -> None:
    if not isinstance(value, dict):
        return
    scope = value.get("scope")
    if not isinstance(scope, dict):
        return
    start_time = scope.get("start_time")
    end_time = scope.get("end_time")
    if not isinstance(start_time, str) or not isinstance(end_time, str):
        return
    start = _parse_rfc3339(start_time, "$.scope.start_time")
    end = _parse_rfc3339(end_time, "$.scope.end_time")
    if start >= end:
        raise _error("$.scope.end_time", "must be after $.scope.start_time")


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: validate_topic_spec.py TOPIC_SPEC_PATH", file=sys.stderr)
        raise SystemExit(2)
    path = Path(arguments[0])
    try:
        value = _load_input(path)
        _validate(value, _load_schema(), "$")
        _validate_scope_time_order(value)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
