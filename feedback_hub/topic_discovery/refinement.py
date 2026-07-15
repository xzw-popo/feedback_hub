"""Targeted review and audit helpers for topic lifecycle refinement."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


_AMBIGUITY_MARKERS = (
    "意图不明确",
    "文本过于简短",
    "未说明具体",
    "未指明具体",
    "无法判断",
    "无上下文",
    "可能为请求",
    "可能涉及",
)

_SELECTION_REASON_ORDER = (
    "baseline_uncertain",
    "baseline_possible_subtopic",
    "low_confidence_same_topic",
    "low_similarity_same_topic",
    "high_similarity_new_topic",
    "many_to_one_same_topic",
    "low_information_candidate",
)


def detect_low_information_review_reasons(topic: dict[str, Any]) -> list[str]:
    text = "\n".join([
        str(topic.get("title") or ""),
        str(topic.get("description") or ""),
    ])
    if any(marker in text for marker in _AMBIGUITY_MARKERS):
        return ["explicit_ambiguity_marker"]
    return []


def select_targeted_topics(
    daily_topics: list[dict[str, Any]],
    baseline_decisions: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    same_confidence_threshold: float = 0.8,
    same_similarity_threshold: float = 0.68,
    new_similarity_threshold: float = 0.78,
) -> list[dict[str, Any]]:
    topic_by_id = _unique_map(daily_topics, "daily_topic_id", "daily topic")
    decision_by_id = _unique_map(baseline_decisions, "daily_topic_id", "baseline decision")
    candidate_by_id = _unique_map(candidate_rows, "daily_topic_id", "candidate row")
    expected_ids = set(topic_by_id)
    if set(decision_by_id) != expected_ids or set(candidate_by_id) != expected_ids:
        raise ValueError("topics, baseline decisions, and candidate rows must have exact coverage")

    same_history_counts = Counter(
        str(decision.get("historical_topic_id") or "")
        for decision in baseline_decisions
        if decision.get("verdict") == "same_topic" and decision.get("historical_topic_id")
    )
    reasons_by_id: dict[str, set[str]] = defaultdict(set)
    for daily_topic_id in sorted(expected_ids):
        topic = topic_by_id[daily_topic_id]
        decision = decision_by_id[daily_topic_id]
        candidate_row = candidate_by_id[daily_topic_id]
        verdict = str(decision.get("verdict") or "")
        selected_similarity = _decision_similarity(decision, candidate_row)
        if verdict == "uncertain":
            reasons_by_id[daily_topic_id].add("baseline_uncertain")
        if verdict == "possible_subtopic":
            reasons_by_id[daily_topic_id].add("baseline_possible_subtopic")
        if verdict == "same_topic":
            if float(decision.get("confidence") or 0.0) < same_confidence_threshold:
                reasons_by_id[daily_topic_id].add("low_confidence_same_topic")
            if selected_similarity < same_similarity_threshold:
                reasons_by_id[daily_topic_id].add("low_similarity_same_topic")
            historical_topic_id = str(decision.get("historical_topic_id") or "")
            if same_history_counts[historical_topic_id] > 1:
                reasons_by_id[daily_topic_id].add("many_to_one_same_topic")
        if verdict == "new_topic" and selected_similarity >= new_similarity_threshold:
            reasons_by_id[daily_topic_id].add("high_similarity_new_topic")
        if detect_low_information_review_reasons(topic):
            reasons_by_id[daily_topic_id].add("low_information_candidate")

    return [
        {
            "daily_topic_id": daily_topic_id,
            "daily_topic": topic_by_id[daily_topic_id],
            "candidates": list(candidate_by_id[daily_topic_id].get("candidates") or []),
            "baseline_decision": decision_by_id[daily_topic_id],
            "selection_reasons": [
                reason for reason in _SELECTION_REASON_ORDER
                if reason in reasons_by_id[daily_topic_id]
            ],
        }
        for daily_topic_id in sorted(reasons_by_id)
        if reasons_by_id[daily_topic_id]
    ]


def _decision_similarity(
    decision: dict[str, Any],
    candidate_row: dict[str, Any],
) -> float:
    candidates = list(candidate_row.get("candidates") or [])
    historical_topic_id = str(decision.get("historical_topic_id") or "")
    selected = next(
        (
            candidate for candidate in candidates
            if str(candidate.get("topic_id") or "") == historical_topic_id
        ),
        None,
    )
    candidate = selected or (candidates[0] if candidates else {})
    try:
        return float(candidate.get("cosine_similarity") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _unique_map(
    rows: list[dict[str, Any]],
    key_name: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(key_name) or "")
        if not key or key in output:
            raise ValueError(f"{label} requires unique non-empty {key_name}")
        output[key] = row
    return output
