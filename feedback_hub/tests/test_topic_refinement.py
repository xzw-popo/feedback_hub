from __future__ import annotations

import numpy as np
import pytest

from feedback_hub.topic_discovery.refinement import (
    audit_refined_decisions,
    audit_low_information_upgrades,
    build_low_information_pool,
    detect_low_information_review_reasons,
    overlay_revised_decisions,
    select_targeted_topics,
)


def _topic(topic_id: str, title: str) -> dict:
    return {
        "daily_topic_id": topic_id,
        "title": title,
        "description": title,
        "conversation_count": 1,
        "feature_candidate_counts": {"unknown_feature": 1},
        "feedback_type_counts": {"bug_problem": 1},
        "platform_counts": {"Win": 1},
        "members": [{"summary": title}],
    }


def _candidate_row(topic: dict, *candidates: tuple[str, float]) -> dict:
    return {
        "daily_topic_id": topic["daily_topic_id"],
        "daily_topic": topic,
        "candidates": [
            {
                "topic_id": topic_id,
                "canonical_title": f"历史-{topic_id}",
                "cosine_similarity": similarity,
            }
            for topic_id, similarity in candidates
        ],
    }


def test_detect_low_information_review_reasons_marks_explicit_ambiguity_only() -> None:
    ambiguous = _topic("d1", "用户提及软件数据备份，意图不明确")
    concrete = _topic("d2", "Win语音输入后文字不上屏")

    assert detect_low_information_review_reasons(ambiguous) == [
        "explicit_ambiguity_marker"
    ]
    assert detect_low_information_review_reasons(concrete) == []


def test_select_targeted_topics_unions_risk_sources_without_duplicates() -> None:
    topics = [
        _topic("d1", "边界不确定"),
        _topic("d2", "可能子主题"),
        _topic("d3", "低置信同主题"),
        _topic("d4", "低相似同主题"),
        _topic("d5", "高相似新主题"),
        _topic("d6", "多对一甲"),
        _topic("d7", "多对一乙"),
        _topic("d8", "用户提及某设置，意图不明确"),
        _topic("d9", "无需复核的明确新主题"),
    ]
    decisions = [
        {"daily_topic_id": "d1", "verdict": "uncertain", "historical_topic_id": "t1", "confidence": 0.5},
        {"daily_topic_id": "d2", "verdict": "possible_subtopic", "historical_topic_id": "t1", "confidence": 0.6},
        {"daily_topic_id": "d3", "verdict": "same_topic", "historical_topic_id": "t2", "confidence": 0.7},
        {"daily_topic_id": "d4", "verdict": "same_topic", "historical_topic_id": "t3", "confidence": 0.9},
        {"daily_topic_id": "d5", "verdict": "new_topic", "historical_topic_id": None, "confidence": 0.8},
        {"daily_topic_id": "d6", "verdict": "same_topic", "historical_topic_id": "t4", "confidence": 0.9},
        {"daily_topic_id": "d7", "verdict": "same_topic", "historical_topic_id": "t4", "confidence": 0.9},
        {"daily_topic_id": "d8", "verdict": "new_topic", "historical_topic_id": None, "confidence": 0.8},
        {"daily_topic_id": "d9", "verdict": "new_topic", "historical_topic_id": None, "confidence": 0.9},
    ]
    candidate_rows = [
        _candidate_row(topics[0], ("t1", 0.74)),
        _candidate_row(topics[1], ("t1", 0.76)),
        _candidate_row(topics[2], ("t2", 0.82)),
        _candidate_row(topics[3], ("t3", 0.65)),
        _candidate_row(topics[4], ("t5", 0.81)),
        _candidate_row(topics[5], ("t4", 0.84)),
        _candidate_row(topics[6], ("t4", 0.83)),
        _candidate_row(topics[7], ("t6", 0.62)),
        _candidate_row(topics[8], ("t7", 0.70)),
    ]

    selected = select_targeted_topics(topics, decisions, candidate_rows)

    assert [row["daily_topic_id"] for row in selected] == [
        "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8"
    ]
    by_id = {row["daily_topic_id"]: row for row in selected}
    assert by_id["d1"]["selection_reasons"] == ["baseline_uncertain"]
    assert by_id["d2"]["selection_reasons"] == ["baseline_possible_subtopic"]
    assert by_id["d3"]["selection_reasons"] == ["low_confidence_same_topic"]
    assert by_id["d4"]["selection_reasons"] == ["low_similarity_same_topic"]
    assert by_id["d5"]["selection_reasons"] == ["high_similarity_new_topic"]
    assert by_id["d6"]["selection_reasons"] == ["many_to_one_same_topic"]
    assert by_id["d7"]["selection_reasons"] == ["many_to_one_same_topic"]
    assert by_id["d8"]["selection_reasons"] == ["low_information_candidate"]
    assert by_id["d1"]["baseline_decision"]["verdict"] == "uncertain"
    assert by_id["d1"]["candidates"][0]["topic_id"] == "t1"


def test_build_low_information_pool_preserves_evidence_and_media() -> None:
    topic = _topic("d1", "用户提及一个异常但未说明对象")
    topic.update({
        "conversation_ids": ["c1"],
        "evidence_links": ["https://example.test/chat"],
        "members": [
            {"summary": "看图", "has_media_evidence": True},
            {"summary": "补充文本", "has_media_evidence": False},
        ],
    })
    decisions = [{
        "daily_topic_id": "d1",
        "verdict": "low_information",
        "historical_topic_id": None,
        "confidence": 0.88,
        "reason": "文本无法确定对象和症状",
    }]

    pool = build_low_information_pool([topic], decisions)

    assert pool[0]["daily_topic_id"] == "d1"
    assert pool[0]["conversation_ids"] == ["c1"]
    assert pool[0]["evidence_links"] == ["https://example.test/chat"]
    assert pool[0]["has_media_evidence"] is True
    assert pool[0]["media_appendix_eligible"] is True
    assert pool[0]["decision_reason"] == "文本无法确定对象和症状"
    assert pool[0]["decision_confidence"] == 0.88
    assert pool[0]["members"] == topic["members"]


def test_low_information_upgrade_audit_flags_similarity_without_merging() -> None:
    current = _topic("d2", "语音输入后文字不上屏")
    current["evidence_links"] = ["https://example.test/current"]
    held = {
        "daily_topic_id": "d1",
        "title": "语音有问题，看图",
        "evidence_links": ["https://example.test/held"],
    }

    rows = audit_low_information_upgrades(
        [current],
        [held],
        np.asarray([[1.0, 0.0]], dtype="float32"),
        np.asarray([[0.8, 0.6]], dtype="float32"),
        threshold=0.78,
    )

    assert rows == [{
        "audit_flag": "possible_low_information_upgrade",
        "current_daily_topic_id": "d2",
        "prior_daily_topic_id": "d1",
        "cosine_similarity": pytest.approx(0.8),
        "current_evidence_links": ["https://example.test/current"],
        "prior_evidence_links": ["https://example.test/held"],
    }]
    assert "topic_id" not in rows[0]


def test_low_information_upgrade_audit_normalizes_vectors_and_sorts() -> None:
    current = [_topic("d2", "甲"), _topic("d3", "乙")]
    held = [{"daily_topic_id": "d1"}]

    rows = audit_low_information_upgrades(
        current,
        held,
        np.asarray([[2.0, 0.0], [1.0, 1.0]], dtype="float32"),
        np.asarray([[3.0, 0.0]], dtype="float32"),
        threshold=0.7,
    )

    assert [row["current_daily_topic_id"] for row in rows] == ["d2", "d3"]
    assert rows[0]["cosine_similarity"] == pytest.approx(1.0)
    assert rows[1]["cosine_similarity"] == pytest.approx(2**-0.5)


def test_overlay_revised_decisions_replaces_only_targeted_rows() -> None:
    baseline = [
        {"daily_topic_id": "d1", "verdict": "new_topic", "confidence": 0.8},
        {"daily_topic_id": "d2", "verdict": "same_topic", "confidence": 0.9},
    ]
    revised = [{
        "daily_topic_id": "d1",
        "verdict": "low_information",
        "historical_topic_id": None,
        "confidence": 0.92,
    }]

    result = overlay_revised_decisions(["d2", "d1"], baseline, revised)

    assert [row["daily_topic_id"] for row in result] == ["d1", "d2"]
    assert [row["verdict"] for row in result] == ["low_information", "same_topic"]
    assert result[0]["decision_source"] == "targeted_refinement"
    assert result[1]["decision_source"] == "baseline_reused"
    assert "decision_source" not in baseline[0]


@pytest.mark.parametrize(
    ("all_ids", "baseline", "revised"),
    [
        (["d1", "d2"], [{"daily_topic_id": "d1"}], []),
        (["d1"], [{"daily_topic_id": "d1"}, {"daily_topic_id": "d1"}], []),
        (["d1"], [{"daily_topic_id": "d1"}], [{"daily_topic_id": "d2"}]),
        (
            ["d1"],
            [{"daily_topic_id": "d1"}],
            [{"daily_topic_id": "d1"}, {"daily_topic_id": "d1"}],
        ),
    ],
)
def test_overlay_revised_decisions_rejects_invalid_coverage(
    all_ids: list[str], baseline: list[dict], revised: list[dict]
) -> None:
    with pytest.raises(ValueError):
        overlay_revised_decisions(all_ids, baseline, revised)


def test_audit_refined_decisions_flags_boundary_risks_without_rewriting() -> None:
    topics = [
        _topic("d1", "语音输入重复输出"),
        _topic("d2", "调整键盘大小"),
        _topic("d3", "Win语音输入不上屏"),
        _topic("d4", "候选词消失"),
        _topic("d5", "新增U模式拆字"),
        _topic("d6", "看图，无法判断"),
        _topic("d7", "语音输入重复两遍"),
    ]
    topics[1].update({
        "feature_candidate_counts": {"keyboard_height_layout": 1},
        "feedback_type_counts": {"feature_request": 1},
    })
    topics[2].update({
        "feature_candidate_counts": {"voice_input": 1},
        "feedback_type_counts": {"bug_problem": 1},
        "platform_counts": {"Win": 1},
    })
    topics[5]["members"] = [{"summary": "看图", "has_media_evidence": True}]
    historical = [
        {
            "topic_id": "t1",
            "canonical_title": "语音输入文字重复",
            "feature_candidate_counts": {"voice_input": 2},
            "feedback_type_counts": {"bug_problem": 2},
            "platform_counts": {"Win": 2},
        },
        {
            "topic_id": "t2",
            "canonical_title": "调整候选词字体大小",
            "feature_candidate_counts": {"candidate_suggestions": 1},
            "feedback_type_counts": {"feature_request": 1},
            "platform_counts": {"Win": 1},
        },
        {
            "topic_id": "t3",
            "canonical_title": "Mac语音输入不上屏",
            "feature_candidate_counts": {"voice_input": 1},
            "feedback_type_counts": {"bug_problem": 1},
            "platform_counts": {"Mac": 1},
        },
        {
            "topic_id": "t4",
            "canonical_title": "候选词消失",
            "feature_candidate_counts": {"candidate_suggestions": 1},
            "feedback_type_counts": {"bug_problem": 1},
            "platform_counts": {"Win": 1},
        },
    ]
    decisions = [
        {"daily_topic_id": "d1", "verdict": "same_topic", "historical_topic_id": "t1", "confidence": 0.9},
        {"daily_topic_id": "d2", "verdict": "same_topic", "historical_topic_id": "t2", "confidence": 0.9},
        {"daily_topic_id": "d3", "verdict": "same_topic", "historical_topic_id": "t3", "confidence": 0.9},
        {"daily_topic_id": "d4", "verdict": "same_topic", "historical_topic_id": "t4", "confidence": 0.7},
        {"daily_topic_id": "d5", "verdict": "new_topic", "historical_topic_id": None, "confidence": 0.9},
        {"daily_topic_id": "d6", "verdict": "low_information", "historical_topic_id": None, "confidence": 0.9},
        {"daily_topic_id": "d7", "verdict": "same_topic", "historical_topic_id": "t1", "confidence": 0.9},
    ]
    candidate_rows = [
        _candidate_row(topics[0], ("t1", 0.88)),
        _candidate_row(topics[1], ("t2", 0.82)),
        _candidate_row(topics[2], ("t3", 0.84)),
        _candidate_row(topics[3], ("t4", 0.81)),
        _candidate_row(topics[4], ("t1", 0.81)),
        _candidate_row(topics[5]),
        _candidate_row(topics[6], ("t1", 0.86)),
    ]

    audit = audit_refined_decisions(topics, historical, decisions, candidate_rows)

    by_id = {row["daily_topic_id"]: row for row in audit}
    assert by_id["d1"]["audit_flags"] == ["many_to_one_same_topic"]
    assert "generic_operation_boundary_risk" in by_id["d2"]["audit_flags"]
    assert "platform_boundary_risk" in by_id["d3"]["audit_flags"]
    assert "low_confidence_same_topic" in by_id["d4"]["audit_flags"]
    assert "high_similarity_new_topic" in by_id["d5"]["audit_flags"]
    assert by_id["d6"]["audit_flags"] == ["low_information_media"]
    assert decisions[0]["verdict"] == "same_topic"
