"""Deterministic rule hints for feedback label v2 experiments.

Rules in this module are intentionally conservative. They can skip only
high-confidence invalid text; otherwise they provide hints for the LLM.
"""
from __future__ import annotations

import re
from typing import Any

from feedback_hub.tagger.v2 import schema

_PUNCT_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)
_NUMBER_RE = re.compile(r"^\d{1,6}$")
_VERSION_RE = re.compile(r"\b\d+(?:\.\d+){1,3}\b")

_TEST_TEXTS = {"test", "TEST", "测试", "ceshi"}
_FILLER_RE = re.compile(r"^(哈+|呵+|嗯+|哦+|啊+)$")

_AREA_TERMS: list[tuple[str, str]] = [
    ("voice_input", "语音"),
    ("voice_input", "听写"),
    ("voice_input", "麦克风"),
    ("emoji_expression", "表情"),
    ("emoji_expression", "emoji"),
    ("emoji_expression", "颜文字"),
    ("ai_generation", "AI"),
    ("ai_generation", "ai"),
    ("ai_generation", "润色"),
    ("ai_generation", "问AI"),
    ("ai_generation", "文字整理"),
    ("clipboard_sync", "剪贴板"),
    ("clipboard_sync", "粘贴"),
    ("keyboard_ui", "键盘"),
    ("keyboard_ui", "工具栏"),
    ("dictionary_phrases", "词库"),
    ("dictionary_phrases", "短语"),
    ("dictionary_phrases", "热词"),
    ("install_update", "安装"),
    ("install_update", "升级"),
    ("cross_app_compatibility", "微信"),
    ("cross_app_compatibility", "高德"),
    ("other_low_priority", "皮肤"),
    ("other_low_priority", "主题"),
]

_ISSUE_TERMS: list[tuple[str, str]] = [
    ("unavailable_or_broken", "不能用"),
    ("unavailable_or_broken", "无法使用"),
    ("unavailable_or_broken", "没反应"),
    ("unavailable_or_broken", "闪退"),
    ("unavailable_or_broken", "崩溃"),
    ("incorrect_or_poor_result", "识别错"),
    ("incorrect_or_poor_result", "不准"),
    ("incorrect_or_poor_result", "错误"),
    ("incorrect_or_poor_result", "推荐差"),
    ("missing_or_unsupported", "不支持"),
    ("missing_or_unsupported", "希望增加"),
    ("missing_or_unsupported", "能不能加"),
    ("hard_to_use_or_trigger", "找不到"),
    ("hard_to_use_or_trigger", "不知道怎么"),
    ("hard_to_use_or_trigger", "入口"),
    ("performance_problem", "卡顿"),
    ("performance_problem", "慢"),
    ("performance_problem", "发热"),
    ("performance_problem", "耗电"),
    ("layout_or_display_problem", "遮挡"),
    ("layout_or_display_problem", "错位"),
    ("layout_or_display_problem", "显示不全"),
    ("compatibility_problem", "兼容"),
    ("compatibility_problem", "适配"),
    ("data_or_sync_problem", "同步失败"),
    ("data_or_sync_problem", "丢失"),
]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _invalid_reason(text: str) -> tuple[str | None, list[str]]:
    stripped = text.strip()
    if not stripped:
        return "empty_text", []
    if stripped in _TEST_TEXTS:
        return "test_text", [stripped]
    if _NUMBER_RE.fullmatch(stripped):
        return "number_only", [stripped]
    if _PUNCT_RE.fullmatch(stripped):
        return "punctuation_only", [stripped]
    if len(stripped) <= 8 and _FILLER_RE.fullmatch(stripped):
        return "filler_text", [stripped]
    return None, []


def _direct_invalid_label(reason: str, evidence: str) -> dict[str, Any]:
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
        "skip_reason": reason,
        "evidence_span": evidence[:120],
        "reason": reason,
        "confidence": 1.0,
        "parse_error": None,
    }


def build_rule_hints(text: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_text = text or ""
    metadata = metadata or {}
    invalid_reason, invalid_terms = _invalid_reason(raw_text)
    if invalid_reason is not None:
        return {
            "skip_llm": True,
            "invalid_reason": invalid_reason,
            "direct_label": _direct_invalid_label(invalid_reason, raw_text.strip()),
            "maybe_product_area": [],
            "maybe_issue_pattern": [],
            "maybe_value_signal": [],
            "detected_platform": metadata.get("platform"),
            "detected_version": metadata.get("appversion"),
            "matched_terms": invalid_terms,
        }

    matched_terms: list[str] = []
    maybe_area: list[str] = []
    maybe_issue: list[str] = []

    for area, term in _AREA_TERMS:
        if term in raw_text:
            maybe_area.append(area)
            matched_terms.append(term)

    for issue, term in _ISSUE_TERMS:
        if term in raw_text:
            maybe_issue.append(issue)
            matched_terms.append(term)

    maybe_value: list[str] = []
    if any(term in raw_text for term in ("上一版", "旧版", "之前版本", "更新后", "升级后")):
        maybe_value.append("regression_suspected")
    if any(term in raw_text for term in ("卸载", "不用了", "换输入法")):
        maybe_value.append("retention_risk")
    if any(term in raw_text for term in ("搜狗", "百度输入法", "系统键盘", "苹果键盘")):
        maybe_value.append("competitor_mentioned")

    detected_version = metadata.get("appversion")
    if detected_version is None:
        version_match = _VERSION_RE.search(raw_text)
        if version_match:
            detected_version = version_match.group(0)

    return {
        "skip_llm": False,
        "invalid_reason": None,
        "direct_label": None,
        "maybe_product_area": _unique([x for x in maybe_area if x in schema.PRODUCT_AREAS]),
        "maybe_issue_pattern": _unique([x for x in maybe_issue if x in schema.ISSUE_PATTERNS]),
        "maybe_value_signal": _unique([x for x in maybe_value if x in schema.VALUE_SIGNALS]),
        "detected_platform": metadata.get("platform"),
        "detected_version": detected_version,
        "matched_terms": _unique(matched_terms),
    }
