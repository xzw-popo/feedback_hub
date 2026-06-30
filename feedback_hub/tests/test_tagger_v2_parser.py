"""Tests for feedback label v2 schema constants."""
from __future__ import annotations

from feedback_hub.tagger.v2 import schema
from feedback_hub.tagger.v2.parser import parse_label_reply


def test_schema_version_is_stable():
    assert schema.SCHEMA_VERSION == "feedback_label_v2"


def test_core_enums_include_low_priority_routing():
    assert "other_low_priority" in schema.PRODUCT_AREAS
    assert "account_sync" in schema.PRODUCT_AREAS
    assert "theme_skin" not in schema.PRODUCT_AREAS
    assert "account_login" not in schema.PRODUCT_AREAS


def test_invalid_feedback_types_skip_detailed_labels():
    assert schema.should_skip_detail("irrelevant_invalid") is True
    assert schema.should_skip_detail("bug_problem") is False
    assert schema.should_skip_detail("feature_request") is False
    assert schema.should_skip_detail("improvement_request") is False


def test_parse_valid_v2_label_from_fenced_json():
    reply = """```json
{
  "schema_version": "feedback_label_v2",
  "feedback_type": "bug_problem",
  "primary_feedback_type": "bug_problem",
  "product_area": ["voice_input"],
  "issue_pattern": ["incorrect_or_poor_result"],
  "evidence_signal": ["has_actual_behavior", "has_context"],
  "value_signal": ["clear_actionable"],
  "observable_impact": "degraded",
  "actionability": "unknown",
  "evidence_span": "语音识别经常错",
  "reason": "语音识别结果不准确",
  "confidence": 0.82
}
```"""
    result = parse_label_reply(reply)
    assert result["schema_version"] == "feedback_label_v2"
    assert result["feedback_type"] == "bug_problem"
    assert result["product_area"] == ["voice_input"]
    assert result["issue_pattern"] == ["incorrect_or_poor_result"]
    assert result["confidence"] == 0.82
    assert result["parse_error"] is None


def test_irrelevant_feedback_clears_detailed_fields():
    result = parse_label_reply(
        '{"feedback_type":"irrelevant_invalid","product_area":["voice_input"],'
        '"issue_pattern":["incorrect_or_poor_result"],"confidence":0.9}'
    )
    assert result["feedback_type"] == "irrelevant_invalid"
    assert result["product_area"] is None
    assert result["issue_pattern"] is None
    assert result["skip_reason"] == "irrelevant_invalid"


def test_required_detail_missing_becomes_unknown():
    result = parse_label_reply(
        '{"feedback_type":"bug_problem","product_area":[],"issue_pattern":[],"confidence":0.7}'
    )
    assert result["product_area"] == ["other_unknown"]
    assert result["issue_pattern"] == ["other_unknown"]


def test_invalid_enum_values_are_filtered():
    result = parse_label_reply(
        '{"feedback_type":"bug_problem","product_area":["theme_skin","voice_input"],'
        '"issue_pattern":["made_up","performance_problem"],"confidence":2}'
    )
    assert result["product_area"] == ["voice_input"]
    assert result["issue_pattern"] == ["performance_problem"]
    assert result["confidence"] == 1.0


def test_no_json_returns_parse_error_fallback():
    result = parse_label_reply("无法处理")
    assert result["feedback_type"] == "irrelevant_invalid"
    assert result["parse_error"] == "no_json_found"
    assert result["confidence"] == 0.0
