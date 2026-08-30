"""Tests for feedback label v2 rule hints."""
from __future__ import annotations

from feedback_hub.tagger.v2.rule_hints import build_rule_hints


def test_empty_text_can_skip_llm_as_invalid():
    hints = build_rule_hints("   ")
    assert hints["skip_llm"] is True
    assert hints["invalid_reason"] == "empty_text"
    assert hints["direct_label"]["feedback_type"] == "irrelevant_invalid"
    assert hints["direct_label"]["product_area"] is None
    assert hints["direct_label"]["issue_pattern"] is None


def test_simple_test_text_can_skip_llm_as_invalid():
    hints = build_rule_hints("测试")
    assert hints["skip_llm"] is True
    assert hints["invalid_reason"] == "test_text"
    assert hints["matched_terms"] == ["测试"]


def test_functional_text_only_produces_hints_not_direct_label():
    hints = build_rule_hints("语音输入经常识别错，上一版没这个问题")
    assert hints["skip_llm"] is False
    assert hints["direct_label"] is None
    assert "voice_input" in hints["maybe_product_area"]
    assert "incorrect_or_poor_result" in hints["maybe_issue_pattern"]
    assert "regression_suspected" in hints["maybe_value_signal"]
    assert "语音" in hints["matched_terms"]


def test_theme_text_is_low_priority_hint_not_direct_label():
    hints = build_rule_hints("希望增加更多皮肤")
    assert hints["skip_llm"] is False
    assert hints["direct_label"] is None
    assert hints["maybe_product_area"] == ["other_low_priority"]
