#!/usr/bin/env python3
"""Validate a feedback-topic specification against the bundled schema."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
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


def _prepare_default_scope_times(
    value: Any,
    default_now: str | None,
    default_days: int | None,
) -> None:
    if not isinstance(value, dict):
        return
    scope = value.get("scope")
    if not isinstance(scope, dict):
        return
    has_start_time = "start_time" in scope
    has_end_time = "end_time" in scope
    if has_start_time != has_end_time:
        raise _error("$.scope", "both start_time and end_time must be provided together")
    if has_start_time or default_now is None or default_days is None:
        return
    end_time = _parse_rfc3339(default_now, "--default-now")
    scope["end_time"] = default_now
    scope["start_time"] = (end_time - timedelta(days=default_days)).isoformat()


def _platform_key(value: str) -> str:
    return "".join(value.split()).casefold()


def _parse_platform_contract(raw: str) -> tuple[tuple[str, ...], dict[str, str]]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise _error("--platform-contract-json", "must be valid JSON") from error
    if not isinstance(value, dict):
        raise _error("--platform-contract-json", "must be an object")
    canonical_raw = value.get("canonical_values")
    expected = ("Win", "Android", "iOS", "Mac")
    if (
        not isinstance(canonical_raw, list)
        or tuple(canonical_raw) != expected
        or any(not isinstance(item, str) for item in canonical_raw)
    ):
        raise _error(
            "--platform-contract-json.canonical_values",
            "must equal Win, Android, iOS, Mac in canonical order",
        )
    if value.get("matching") != "case_insensitive_ignore_whitespace":
        raise _error(
            "--platform-contract-json.matching",
            "must equal case_insensitive_ignore_whitespace",
        )
    aliases_raw = value.get("aliases")
    if not isinstance(aliases_raw, dict) or not aliases_raw:
        raise _error(
            "--platform-contract-json.aliases", "must be a non-empty object",
        )
    aliases: dict[str, str] = {}
    for alias, target in aliases_raw.items():
        if (
            not isinstance(alias, str)
            or not alias.strip()
            or not isinstance(target, str)
        ):
            raise _error(
                "--platform-contract-json.aliases",
                "must map non-empty strings to canonical platform strings",
            )
        if target not in expected:
            raise _error(
                f"--platform-contract-json.aliases.{alias}",
                "must target a canonical platform",
            )
        key = _platform_key(alias)
        if key in aliases and aliases[key] != target:
            raise _error(
                "--platform-contract-json.aliases",
                "contains conflicting normalized aliases",
            )
        aliases[key] = target
    for canonical in expected:
        if aliases.get(_platform_key(canonical)) != canonical:
            raise _error(
                "--platform-contract-json.aliases",
                f"must contain the canonical self-alias: {canonical}",
            )
    return expected, aliases


def _prepare_platform_scope(value: Any, raw_contract: str | None) -> None:
    if raw_contract is None or not isinstance(value, dict):
        return
    canonical_values, aliases = _parse_platform_contract(raw_contract)
    scope = value.get("scope")
    if not isinstance(scope, dict):
        return
    platforms = scope.get("platforms", [])
    if (
        not isinstance(platforms, list)
        or any(not isinstance(item, str) for item in platforms)
    ):
        return
    normalized: list[str] = []
    seen: set[str] = set()
    for index, platform in enumerate(platforms):
        canonical = aliases.get(_platform_key(platform))
        if canonical is None:
            supported = ", ".join(canonical_values)
            raise _error(
                f"$.scope.platforms[{index}]",
                f"unsupported platform: {platform}; supported platforms: {supported}",
            )
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    scope["platforms"] = normalized


def _write_prepared_spec(path: Path, source_path: Path, raw: bytes) -> None:
    if path.suffix.lower() != ".json":
        raise _error("--output", "prepared topic spec path must end in .json")
    if path.resolve(strict=False) == source_path.resolve(strict=False):
        raise _error("--output", "must differ from topic spec path")
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise _error("--output", f"cannot write prepared topic spec: {error.strerror or 'I/O error'}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--default-now", metavar="RFC3339")
    parser.add_argument("--default-days", type=int, metavar="DAYS")
    parser.add_argument("--platform-contract-json", metavar="JSON")
    parser.add_argument("--output", metavar="PREPARED_SPEC_PATH")
    parser.add_argument("topic_spec_path", metavar="TOPIC_SPEC_PATH")
    args = parser.parse_args(arguments)
    path = Path(args.topic_spec_path)
    try:
        value = _load_input(path)
        _prepare_default_scope_times(value, args.default_now, args.default_days)
        _prepare_platform_scope(value, args.platform_contract_json)
        _validate(value, _load_schema(), "$")
        _validate_scope_time_order(value)
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if args.output:
            _write_prepared_spec(Path(args.output), path, (rendered + "\n").encode("utf-8"))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
    print(rendered)


if __name__ == "__main__":
    main()
