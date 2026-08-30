"""Targeted review and audit helpers for topic lifecycle refinement."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

import numpy as np


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

_AUDIT_FLAG_ORDER = (
    "many_to_one_same_topic",
    "generic_operation_boundary_risk",
    "platform_boundary_risk",
    "low_confidence_same_topic",
    "high_similarity_new_topic",
    "low_information_media",
)

_GENERIC_OPERATION_GROUPS = (
    ("sorting", ("排序", "sort")),
    ("sizing", ("大小", "尺寸", "高度", "字号", "字体大小", "size")),
    ("switches", ("开关", "开启", "关闭", "启用", "禁用", "switch")),
    ("entry_points", ("入口", "快捷键", "唤起", "entry", "shortcut")),
    ("visibility", ("显示", "隐藏", "可见", "visibility")),
    ("synchronization", ("同步", "sync")),
    ("customization", ("自定义", "定制", "custom")),
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


def build_low_information_pool(
    daily_topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    topic_by_id = _unique_map(daily_topics, "daily_topic_id", "daily topic")
    decision_by_id = _unique_map(decisions, "daily_topic_id", "decision")
    unknown_ids = set(decision_by_id) - set(topic_by_id)
    if unknown_ids:
        raise ValueError(f"decisions reference unknown daily topics: {sorted(unknown_ids)}")

    pool = []
    for daily_topic_id in sorted(decision_by_id):
        decision = decision_by_id[daily_topic_id]
        if str(decision.get("verdict") or "") != "low_information":
            continue
        topic = deepcopy(topic_by_id[daily_topic_id])
        has_media = any(
            bool(member.get("has_media_evidence"))
            for member in topic.get("members") or []
            if isinstance(member, dict)
        )
        topic.update({
            "decision_reason": str(decision.get("reason") or "").strip(),
            "decision_confidence": float(decision.get("confidence") or 0.0),
            "has_media_evidence": has_media,
            "media_appendix_eligible": has_media,
        })
        pool.append(topic)
    return pool


def overlay_revised_decisions(
    all_daily_topic_ids: list[str],
    baseline_decisions: list[dict[str, Any]],
    revised_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_ids = [str(value) for value in all_daily_topic_ids]
    if not expected_ids or len(expected_ids) != len(set(expected_ids)) or any(not value for value in expected_ids):
        raise ValueError("all_daily_topic_ids must be unique and non-empty")
    baseline_by_id = _unique_map(
        baseline_decisions, "daily_topic_id", "baseline decision"
    )
    revised_by_id = _unique_map(
        revised_decisions, "daily_topic_id", "revised decision"
    ) if revised_decisions else {}
    expected = set(expected_ids)
    if set(baseline_by_id) != expected:
        raise ValueError("baseline decisions must exactly cover all daily topics")
    if not set(revised_by_id).issubset(expected):
        raise ValueError("revised decisions contain unknown daily topics")

    output = []
    for daily_topic_id in sorted(expected):
        if daily_topic_id in revised_by_id:
            decision = deepcopy(revised_by_id[daily_topic_id])
            decision["decision_source"] = "targeted_refinement"
        else:
            decision = deepcopy(baseline_by_id[daily_topic_id])
            decision["decision_source"] = "baseline_reused"
        output.append(decision)
    return output


def audit_low_information_upgrades(
    current_topics: list[dict[str, Any]],
    prior_low_information: list[dict[str, Any]],
    current_embeddings: np.ndarray,
    prior_embeddings: np.ndarray,
    *,
    threshold: float = 0.78,
) -> list[dict[str, Any]]:
    if current_embeddings.ndim != 2 or current_embeddings.shape[0] != len(current_topics):
        raise ValueError("current embedding rows must match current topics")
    if prior_embeddings.ndim != 2 or prior_embeddings.shape[0] != len(prior_low_information):
        raise ValueError("prior embedding rows must match prior low-information topics")
    if current_embeddings.shape[1] != prior_embeddings.shape[1]:
        raise ValueError("upgrade embedding dimensions must match")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("upgrade threshold must be between 0 and 1")
    if not np.isfinite(current_embeddings).all() or not np.isfinite(prior_embeddings).all():
        raise ValueError("upgrade embeddings must be finite")
    if not current_topics or not prior_low_information:
        return []

    current_vectors = _normalize_rows(current_embeddings)
    prior_vectors = _normalize_rows(prior_embeddings)
    similarities = np.einsum(
        "ik,jk->ij",
        current_vectors.astype("float64"),
        prior_vectors.astype("float64"),
        dtype=np.float64,
    )
    rows = []
    for current_index, current in enumerate(current_topics):
        current_id = str(current.get("daily_topic_id") or "")
        if not current_id:
            raise ValueError("current topic requires daily_topic_id")
        for prior_index, prior in enumerate(prior_low_information):
            prior_id = str(prior.get("daily_topic_id") or "")
            if not prior_id:
                raise ValueError("prior low-information topic requires daily_topic_id")
            similarity = float(similarities[current_index, prior_index])
            if similarity < threshold:
                continue
            rows.append({
                "audit_flag": "possible_low_information_upgrade",
                "current_daily_topic_id": current_id,
                "prior_daily_topic_id": prior_id,
                "cosine_similarity": similarity,
                "current_evidence_links": _dedupe_strings(current.get("evidence_links") or []),
                "prior_evidence_links": _dedupe_strings(prior.get("evidence_links") or []),
            })
    return sorted(
        rows,
        key=lambda row: (
            -float(row["cosine_similarity"]),
            str(row["current_daily_topic_id"]),
            str(row["prior_daily_topic_id"]),
        ),
    )


def audit_refined_decisions(
    daily_topics: list[dict[str, Any]],
    historical_topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    low_confidence_threshold: float = 0.8,
    high_similarity_threshold: float = 0.78,
) -> list[dict[str, Any]]:
    topic_by_id = _unique_map(daily_topics, "daily_topic_id", "daily topic")
    history_by_id = _unique_map(historical_topics, "topic_id", "historical topic")
    decision_by_id = _unique_map(decisions, "daily_topic_id", "decision")
    candidate_by_id = _unique_map(candidate_rows, "daily_topic_id", "candidate row")
    expected = set(topic_by_id)
    if set(decision_by_id) != expected or set(candidate_by_id) != expected:
        raise ValueError("topics, decisions, and candidate rows must have exact coverage")

    same_counts = Counter(
        str(decision.get("historical_topic_id") or "")
        for decision in decisions
        if decision.get("verdict") == "same_topic" and decision.get("historical_topic_id")
    )
    audit_rows = []
    for daily_topic_id in sorted(expected):
        topic = topic_by_id[daily_topic_id]
        decision = decision_by_id[daily_topic_id]
        candidate_row = candidate_by_id[daily_topic_id]
        verdict = str(decision.get("verdict") or "")
        selected_history_id = str(decision.get("historical_topic_id") or "") or None
        candidates = list(candidate_row.get("candidates") or [])
        if selected_history_id is None and candidates:
            selected_history_id = str(candidates[0].get("topic_id") or "") or None
        history = history_by_id.get(str(selected_history_id or ""), {})
        similarity = _decision_similarity(decision, candidate_row)
        flags = set()

        if verdict == "same_topic" and same_counts[str(decision.get("historical_topic_id") or "")] > 1:
            flags.add("many_to_one_same_topic")
        if verdict in {"same_topic", "possible_subtopic"}:
            if _has_shared_generic_operation(topic, history) and _features_are_disjoint(topic, history):
                flags.add("generic_operation_boundary_risk")
            if _is_cross_platform_bug(topic, history):
                flags.add("platform_boundary_risk")
        if verdict == "same_topic" and float(decision.get("confidence") or 0.0) < low_confidence_threshold:
            flags.add("low_confidence_same_topic")
        if verdict == "new_topic" and similarity >= high_similarity_threshold:
            flags.add("high_similarity_new_topic")
        has_media = _has_media(topic)
        if verdict == "low_information" and has_media:
            flags.add("low_information_media")
        if not flags:
            continue

        audit_rows.append({
            "daily_topic_id": daily_topic_id,
            "verdict": verdict,
            "selected_historical_topic_id": selected_history_id,
            "selected_similarity": similarity,
            "audit_flags": [flag for flag in _AUDIT_FLAG_ORDER if flag in flags],
            "current_title": str(topic.get("title") or "").strip(),
            "historical_title": str(
                history.get("canonical_title") or history.get("title") or ""
            ).strip(),
            "evidence_link": _first_evidence_link(topic),
            "has_media_evidence": has_media,
        })
    return audit_rows


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


def _has_shared_generic_operation(
    current: dict[str, Any], historical: dict[str, Any]
) -> bool:
    current_text = "\n".join([
        str(current.get("title") or ""),
        str(current.get("description") or ""),
    ]).lower()
    historical_text = "\n".join([
        str(historical.get("canonical_title") or historical.get("title") or ""),
        str(historical.get("canonical_description") or historical.get("description") or ""),
    ]).lower()
    return any(
        any(alias in current_text for alias in aliases)
        and any(alias in historical_text for alias in aliases)
        for _name, aliases in _GENERIC_OPERATION_GROUPS
    )


def _features_are_disjoint(
    current: dict[str, Any], historical: dict[str, Any]
) -> bool:
    return bool(
        (current_features := _count_keys(current, "feature_candidate_counts", {"unknown_feature"}))
        and (historical_features := _count_keys(historical, "feature_candidate_counts", {"unknown_feature"}))
        and current_features.isdisjoint(historical_features)
    )


def _is_cross_platform_bug(
    current: dict[str, Any], historical: dict[str, Any]
) -> bool:
    current_types = _count_keys(current, "feedback_type_counts", set())
    historical_types = _count_keys(historical, "feedback_type_counts", set())
    is_bug = bool({"bug_problem", "mixed"} & current_types) and bool(
        {"bug_problem", "mixed"} & historical_types
    )
    current_platforms = _count_keys(current, "platform_counts", {"unknown", "unknown_platform"})
    historical_platforms = _count_keys(historical, "platform_counts", {"unknown", "unknown_platform"})
    return bool(
        is_bug
        and current_platforms
        and historical_platforms
        and current_platforms.isdisjoint(historical_platforms)
    )


def _count_keys(
    row: dict[str, Any], field_name: str, excluded: set[str]
) -> set[str]:
    counts = row.get(field_name) or {}
    if not isinstance(counts, dict):
        return set()
    return {
        str(key)
        for key, value in counts.items()
        if str(key) not in excluded and bool(value)
    }


def _has_media(topic: dict[str, Any]) -> bool:
    return bool(topic.get("has_media_evidence")) or any(
        bool(member.get("has_media_evidence"))
        for member in topic.get("members") or []
        if isinstance(member, dict)
    )


def _first_evidence_link(topic: dict[str, Any]) -> str:
    direct = [str(value) for value in topic.get("evidence_links") or [] if value]
    if direct:
        return direct[0]
    for member in topic.get("members") or []:
        if not isinstance(member, dict):
            continue
        links = [str(value) for value in member.get("evidence_links") or [] if value]
        if links:
            return links[0]
    return ""


def _normalize_rows(values: np.ndarray) -> np.ndarray:
    vectors = values.astype("float64")
    norms = np.linalg.norm(vectors, axis=1)
    if len(norms) and np.any(norms <= 1e-12):
        raise ValueError("upgrade embeddings cannot contain zero vectors")
    return (vectors / norms[:, None]).astype("float32")


def _dedupe_strings(values: list[Any]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


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
