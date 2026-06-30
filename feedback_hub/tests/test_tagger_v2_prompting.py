"""Tests for feedback label v2 prompt rendering."""
from __future__ import annotations

from feedback_hub.tagger.v2.prompting import build_prompt


def test_build_prompt_includes_feedback_text_and_schema():
    prompt = build_prompt("语音识别经常错", metadata={"platform": "iOS", "appversion": "3.2.1"})
    assert "语音识别经常错" in prompt
    assert "feedback_label_v2" in prompt
    assert "iOS" in prompt
    assert "product_area" in prompt
    assert "issue_pattern" in prompt


def test_build_prompt_escapes_braces_in_feedback_text():
    prompt = build_prompt("输入 {test} 会异常", metadata={})
    assert "输入 {test} 会异常" in prompt


def test_build_prompt_includes_tightened_v02_rules():
    prompt = build_prompt("符号库怎么快捷调出", metadata={})
    assert "question_help" in prompt
    assert "leave `issue_pattern` empty" in prompt
    assert "Do not add `vague`" in prompt
    assert "requires a concrete product, support, or engineering action" in prompt
    assert "needs_review" in prompt
