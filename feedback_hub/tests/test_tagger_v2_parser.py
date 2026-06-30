"""Tests for feedback label v2 schema constants."""
from __future__ import annotations

from feedback_hub.tagger.v2 import schema


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
