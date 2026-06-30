"""Parse and validate feedback label v2 LLM replies."""
from __future__ import annotations

import json
import re
from typing import Any

from feedback_hub.tagger.v2 import schema

_FENCE_JSON_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_FENCE_ANY_RE = re.compile(r"```\s*(.+?)\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_str(reply: str) -> str | None:
    if not reply:
        return None
    match = _FENCE_JSON_RE.search(reply)
    if match:
        return match.group(1).strip()
    match = _FENCE_ANY_RE.search(reply)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    match = _BRACE_RE.search(reply)
    if match:
        return match.group(0).strip()
    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    if isinstance(value, str):
        return [value]
    return []


def _filter(values: Any, allowed: set[str]) -> list[str]:
    return [x for x in _as_list(values) if x in allowed]


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except Exception:
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _text(value: Any, max_len: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:max_len]


def _fallback(parse_error: str) -> dict[str, Any]:
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "feedback_type": "irrelevant_invalid",
        "primary_feedback_type": "irrelevant_invalid",
        "product_area": None,
        "issue_pattern": None,
        "evidence_signal": ["vague"],
        "value_signal": [],
        "observable_impact": "unknown",
        "actionability": "insufficient_info",
        "skip_reason": "parse_error",
        "evidence_span": "",
        "reason": "parse_error",
        "confidence": 0.0,
        "parse_error": parse_error,
    }


def parse_label_reply(reply: str) -> dict[str, Any]:
    raw = _extract_json_str(reply or "")
    if raw is None:
        return _fallback("no_json_found")
    try:
        obj = json.loads(raw)
    except Exception as exc:
        return _fallback(type(exc).__name__)
    if not isinstance(obj, dict):
        return _fallback("not_object")

    feedback_type = obj.get("feedback_type")
    if feedback_type not in schema.FEEDBACK_TYPES:
        feedback_type = "irrelevant_invalid"

    primary = obj.get("primary_feedback_type")
    if primary not in schema.FEEDBACK_TYPES:
        primary = feedback_type

    evidence_signal = _filter(obj.get("evidence_signal"), schema.EVIDENCE_SIGNALS)
    if not evidence_signal:
        evidence_signal = ["vague"]

    value_signal = _filter(obj.get("value_signal"), schema.VALUE_SIGNALS)

    observable_impact = obj.get("observable_impact")
    if observable_impact not in schema.OBSERVABLE_IMPACTS:
        observable_impact = "unknown"

    actionability = obj.get("actionability")
    if actionability not in schema.ACTIONABILITY_VALUES:
        actionability = "unknown"

    product_area = _filter(obj.get("product_area"), schema.PRODUCT_AREAS)
    issue_pattern = _filter(obj.get("issue_pattern"), schema.ISSUE_PATTERNS)
    skip_reason = None

    if schema.should_skip_detail(feedback_type):
        product_area_out: list[str] | None = None
        issue_pattern_out: list[str] | None = None
        skip_reason = "irrelevant_invalid"
    else:
        product_area_out = product_area
        issue_pattern_out = issue_pattern
        if feedback_type in schema.DETAIL_REQUIRED_TYPES:
            if not product_area_out:
                product_area_out = ["other_unknown"]
            if not issue_pattern_out:
                issue_pattern_out = ["other_unknown"]

    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "feedback_type": feedback_type,
        "primary_feedback_type": primary,
        "product_area": product_area_out,
        "issue_pattern": issue_pattern_out,
        "evidence_signal": evidence_signal,
        "value_signal": value_signal,
        "observable_impact": observable_impact,
        "actionability": actionability,
        "evidence_span": _text(obj.get("evidence_span"), 120),
        "reason": _text(obj.get("reason"), 120) or "llm",
        "confidence": _clamp_confidence(obj.get("confidence", 0.0)),
        "parse_error": None,
    }
    if skip_reason:
        result["skip_reason"] = skip_reason
    return result
