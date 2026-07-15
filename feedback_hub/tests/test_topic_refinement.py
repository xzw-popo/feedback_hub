from __future__ import annotations

from feedback_hub.topic_discovery.refinement import (
    detect_low_information_review_reasons,
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
