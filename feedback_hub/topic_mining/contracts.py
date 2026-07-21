"""Canonical, dependency-free contract for feedback topic specifications."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping


_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "topic_name",
        "objective",
        "scope",
        "unit",
        "mode",
        "inclusion_criteria",
        "exclusion_criteria",
        "positive_examples",
        "negative_examples",
        "lexical_hints",
        "classification_labels",
        "output",
    }
)
_REQUIRED_TOP_LEVEL_FIELDS = _TOP_LEVEL_FIELDS - {"mode"}
_SCOPE_FIELDS = frozenset(
    {"start_time", "end_time", "platforms", "products", "channels", "versions"}
)
_OUTPUT_FIELDS = frozenset({"preferred_format", "required_fields"})
SUPPORTED_OUTPUT_FIELDS = frozenset(
    {"feedback_text", "feedback_time", "source_url"}
)
_LABEL_FIELDS = frozenset({"id", "meaning"})
_LEXICAL_HINT_FIELDS = frozenset({"objects", "contexts"})
_RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
_RFC3339_LOCAL_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?$")


@dataclass(frozen=True)
class TopicScope:
    start_time: datetime
    end_time: datetime
    platforms: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopicSpec:
    schema_version: int
    topic_name: str
    objective: str
    scope: TopicScope
    unit: str
    mode: Literal["standard", "exhaustive"]
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    lexical_hints: dict[str, tuple[str, ...]]
    classification_labels: tuple[dict[str, str], ...]
    output: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic_name": self.topic_name,
            "objective": self.objective,
            "scope": {
                "start_time": self.scope.start_time.isoformat(),
                "end_time": self.scope.end_time.isoformat(),
                "platforms": list(self.scope.platforms),
                "products": list(self.scope.products),
                "channels": list(self.scope.channels),
                "versions": list(self.scope.versions),
            },
            "unit": self.unit,
            "mode": self.mode,
            "inclusion_criteria": list(self.inclusion_criteria),
            "exclusion_criteria": list(self.exclusion_criteria),
            "positive_examples": list(self.positive_examples),
            "negative_examples": list(self.negative_examples),
            "lexical_hints": {key: list(value) for key, value in sorted(self.lexical_hints.items())},
            "classification_labels": [dict(value) for value in self.classification_labels],
            "output": dict(self.output),
        }


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {', '.join(sorted(unknown))}")


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _string_list(value: Any, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _required_string(item, f"{field}[{index}]")
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    if required and not normalized:
        raise ValueError(f"{field} must not be empty")
    return tuple(normalized)


def _aware_datetime(value: Any, field: str) -> datetime:
    # Unlike semantic text fields, RFC3339 timestamps are syntax tokens.
    # Preserve raw input so leading/trailing whitespace is rejected, not trimmed.
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    text = value
    if not _RFC3339_DATETIME.fullmatch(text):
        if _RFC3339_LOCAL_DATETIME.fullmatch(text):
            raise ValueError(f"{field} must include a timezone")
        raise ValueError(f"{field} must be an RFC3339 datetime")
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC3339 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def validate_topic_spec(raw: Mapping[str, Any]) -> TopicSpec:
    """Validate untrusted input and return its normalized canonical representation."""
    raw = _require_mapping(raw, "topic_spec")
    _reject_unknown(raw, _TOP_LEVEL_FIELDS, "topic_spec")
    missing = _REQUIRED_TOP_LEVEL_FIELDS - set(raw)
    if missing:
        raise ValueError(f"topic_spec is missing required fields: {', '.join(sorted(missing))}")

    schema_version = raw["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ValueError("schema_version must be 1")

    scope_raw = _require_mapping(raw["scope"], "scope")
    _reject_unknown(scope_raw, _SCOPE_FIELDS, "scope")
    missing_scope = {"start_time", "end_time"} - set(scope_raw)
    if missing_scope:
        raise ValueError(f"scope is missing required fields: {', '.join(sorted(missing_scope))}")
    start_time = _aware_datetime(scope_raw["start_time"], "scope.start_time")
    end_time = _aware_datetime(scope_raw["end_time"], "scope.end_time")
    if start_time >= end_time:
        raise ValueError("scope.end_time must be after scope.start_time")
    scope = TopicScope(
        start_time=start_time,
        end_time=end_time,
        platforms=_string_list(scope_raw.get("platforms", []), "scope.platforms"),
        products=_string_list(scope_raw.get("products", []), "scope.products"),
        channels=_string_list(scope_raw.get("channels", []), "scope.channels"),
        versions=_string_list(scope_raw.get("versions", []), "scope.versions"),
    )

    unit = _required_string(raw["unit"], "unit")
    if unit not in {"feedback", "conversation"}:
        raise ValueError("unit must be 'feedback' or 'conversation'")
    mode = raw.get("mode", "standard")
    if mode not in {"standard", "exhaustive"}:
        raise ValueError("mode must be standard or exhaustive")

    lexical_raw = _require_mapping(raw["lexical_hints"], "lexical_hints")
    _reject_unknown(lexical_raw, _LEXICAL_HINT_FIELDS, "lexical_hints")
    lexical_hints = {
        _required_string(key, "lexical_hints key"): _string_list(value, f"lexical_hints.{key}")
        for key, value in lexical_raw.items()
    }

    labels_raw = raw["classification_labels"]
    if not isinstance(labels_raw, list) or not labels_raw:
        raise ValueError("classification_labels must be a non-empty list")
    labels: list[dict[str, str]] = []
    label_ids: set[str] = set()
    for index, label_raw in enumerate(labels_raw):
        label = _require_mapping(label_raw, f"classification_labels[{index}]")
        _reject_unknown(label, _LABEL_FIELDS, f"classification_labels[{index}]")
        if set(label) != _LABEL_FIELDS:
            raise ValueError(f"classification_labels[{index}] must contain id and meaning")
        label_id = _required_string(label["id"], f"classification_labels[{index}].id")
        if label_id in label_ids:
            raise ValueError(f"classification_labels has duplicate id: {label_id}")
        label_ids.add(label_id)
        labels.append({"id": label_id, "meaning": _required_string(label["meaning"], f"classification_labels[{index}].meaning")})
    if label_ids != {"matched", "not_matched"}:
        raise ValueError("classification_labels ids must be exactly matched and not_matched")

    output_raw = _require_mapping(raw["output"], "output")
    _reject_unknown(output_raw, _OUTPUT_FIELDS, "output")
    if set(output_raw) != _OUTPUT_FIELDS:
        raise ValueError("output must contain preferred_format and required_fields")
    preferred_format = _required_string(output_raw["preferred_format"], "output.preferred_format")
    if preferred_format not in {"xlsx", "jsonl"}:
        raise ValueError("output.preferred_format must be xlsx or jsonl")
    required_fields = _string_list(
        output_raw["required_fields"], "output.required_fields", required=True,
    )
    unsupported_output_fields = set(required_fields) - SUPPORTED_OUTPUT_FIELDS
    if unsupported_output_fields:
        raise ValueError(
            "output.required_fields contains unsupported fields: "
            + ", ".join(sorted(unsupported_output_fields))
        )
    output = {
        "preferred_format": preferred_format,
        "required_fields": list(required_fields),
    }

    return TopicSpec(
        schema_version=1,
        topic_name=_required_string(raw["topic_name"], "topic_name"),
        objective=_required_string(raw["objective"], "objective"),
        scope=scope,
        unit=unit,
        mode=mode,
        inclusion_criteria=_string_list(raw["inclusion_criteria"], "inclusion_criteria", required=True),
        exclusion_criteria=_string_list(raw["exclusion_criteria"], "exclusion_criteria", required=True),
        positive_examples=_string_list(raw["positive_examples"], "positive_examples"),
        negative_examples=_string_list(raw["negative_examples"], "negative_examples"),
        lexical_hints=lexical_hints,
        classification_labels=tuple(labels),
        output=output,
    )


def topic_spec_hash(spec: TopicSpec) -> str:
    """Return the stable SHA-256 identity for a canonical topic specification."""
    encoded = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def topic_spec_json_schema() -> dict[str, Any]:
    """Return the strict JSON Schema accepted by :func:`validate_topic_spec`."""
    string_array = {"type": "array", "items": {"type": "string", "minLength": 1}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_REQUIRED_TOP_LEVEL_FIELDS),
        "properties": {
            "schema_version": {"const": 1},
            "topic_name": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "scope": {
                "type": "object",
                "additionalProperties": False,
                "required": ["start_time", "end_time"],
                "properties": {
                    "start_time": {"type": "string", "format": "date-time"},
                    "end_time": {"type": "string", "format": "date-time"},
                    "platforms": string_array,
                    "products": string_array,
                    "channels": string_array,
                    "versions": string_array,
                },
            },
            "unit": {"enum": ["feedback", "conversation"]},
            "mode": {"enum": ["standard", "exhaustive"]},
            "inclusion_criteria": {**string_array, "minItems": 1},
            "exclusion_criteria": {**string_array, "minItems": 1},
            "positive_examples": string_array,
            "negative_examples": string_array,
            "lexical_hints": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "objects": string_array,
                    "contexts": string_array,
                },
            },
            "classification_labels": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "meaning"],
                    "properties": {
                        "id": {"enum": ["matched", "not_matched"]},
                        "meaning": {"type": "string", "minLength": 1},
                    },
                },
                "contains": {"type": "object", "properties": {"id": {"const": "matched"}}},
                "allOf": [
                    {"contains": {"type": "object", "properties": {"id": {"const": "not_matched"}}}}
                ],
            },
            "output": {
                "type": "object",
                "additionalProperties": False,
                "required": ["preferred_format", "required_fields"],
                "properties": {
                    "preferred_format": {"enum": ["xlsx", "jsonl"]},
                    "required_fields": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"enum": sorted(SUPPORTED_OUTPUT_FIELDS)},
                    },
                },
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the feedback topic specification JSON Schema.")
    parser.add_argument("--write-schema", type=Path, required=True, metavar="PATH")
    args = parser.parse_args()
    args.write_schema.write_text(
        json.dumps(topic_spec_json_schema(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
