"""Derive deterministic daily-report signals from completed topic artifacts."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Iterable

from feedback_hub.tagger.v2.feature_catalog import FeatureCatalog


def build_topic_signal_features(
    days: list[dict[str, Any]],
    final_topic_store: list[dict[str, Any]],
    catalog: FeatureCatalog,
    *,
    report_date: str,
) -> list[dict[str, Any]]:
    """Build one auditable feature row for every report-day atomic topic."""
    parsed_report_date = date.fromisoformat(report_date)
    day_by_date = _unique_map(days, "date", "day")
    if report_date not in day_by_date:
        raise ValueError(f"missing report day: {report_date}")

    daily_topic_rows: dict[str, tuple[str, dict[str, Any]]] = {}
    for day_value, day_row in day_by_date.items():
        if date.fromisoformat(day_value) > parsed_report_date:
            continue
        for topic in day_row.get("daily_topics") or []:
            daily_topic_id = str(topic.get("daily_topic_id") or "")
            if not daily_topic_id:
                raise ValueError("daily_topic_id is required")
            if daily_topic_id in daily_topic_rows:
                raise ValueError(f"duplicate daily_topic_id: {daily_topic_id}")
            daily_topic_rows[daily_topic_id] = (day_value, topic)

    stable_by_id = _unique_map(final_topic_store, "topic_id", "stable topic")
    stable_id_by_daily_id: dict[str, str] = {}
    for stable_topic_id, topic in stable_by_id.items():
        for daily_topic_id in _dedupe(topic.get("source_daily_topic_ids") or []):
            if daily_topic_id in stable_id_by_daily_id:
                raise ValueError(f"daily topic appears in multiple stable topics: {daily_topic_id}")
            stable_id_by_daily_id[daily_topic_id] = stable_topic_id

    report_day = day_by_date[report_date]
    report_topics = list(report_day.get("daily_topics") or [])
    issue_by_id = _unique_map(report_day.get("issue_units") or [], "issue_unit_id", "issue unit")
    decision_by_id = _unique_map(
        report_day.get("lifecycle_decisions") or [],
        "daily_topic_id",
        "lifecycle decision",
    )
    baseline_dates = [
        (parsed_report_date - timedelta(days=offset)).isoformat()
        for offset in range(7, 0, -1)
    ]

    features: list[dict[str, Any]] = []
    for topic in report_topics:
        daily_topic_id = str(topic.get("daily_topic_id") or "")
        member_ids = _dedupe(topic.get("member_issue_unit_ids") or [])
        unknown_members = [issue_id for issue_id in member_ids if issue_id not in issue_by_id]
        if unknown_members:
            raise ValueError(
                f"topic {daily_topic_id} contains unknown issue members: {', '.join(unknown_members)}"
            )
        member_units = [issue_by_id[issue_id] for issue_id in member_ids]
        stable_topic_id = stable_id_by_daily_id.get(daily_topic_id)
        stable_topic = stable_by_id.get(stable_topic_id or "", {})
        source_daily_ids = _dedupe(stable_topic.get("source_daily_topic_ids") or [daily_topic_id])
        daily_counts: dict[str, int] = {}
        historical_active_dates: list[str] = []
        for source_daily_id in source_daily_ids:
            source = daily_topic_rows.get(source_daily_id)
            if source is None:
                continue
            source_date, source_topic = source
            count = len(set(_strings(source_topic.get("conversation_ids") or [])))
            daily_counts[source_date] = daily_counts.get(source_date, 0) + count
            if source_date < report_date and count:
                historical_active_dates.append(source_date)

        decision = decision_by_id.get(daily_topic_id, {})
        source_links = _dedupe(
            link
            for unit in member_units
            for link in _strings(unit.get("evidence_links") or [])
        )
        evidence_spans = [
            span
            for unit in member_units
            for span in _strings(unit.get("evidence_spans") or [])
        ]
        feature_policy = _resolve_feature_policy(member_units, catalog)
        feedback_type_counts = Counter(str(unit.get("feedback_type") or "") for unit in member_units)
        platform_counts = Counter(str(unit.get("platform") or "") for unit in member_units)
        appversion_counts = Counter(str(unit.get("appversion") or "") for unit in member_units)
        feature_counts = Counter(str(unit.get("feature_id") or "unknown_feature") for unit in member_units)

        features.append({
            "daily_topic_id": daily_topic_id,
            "stable_topic_id": stable_topic_id,
            "parent_topic_ids": _dedupe(stable_topic.get("parent_topic_ids") or []),
            "title": str(topic.get("title") or "").strip(),
            "description": str(topic.get("description") or "").strip(),
            "today_conversation_count": len(set(_strings(topic.get("conversation_ids") or []))),
            "today_issue_unit_count": len(member_ids),
            "baseline_dates": baseline_dates,
            "baseline_daily_counts": [daily_counts.get(value, 0) for value in baseline_dates],
            "historical_active_dates": sorted(set(historical_active_dates)),
            "lifecycle_verdict": str(decision.get("verdict") or ""),
            "lifecycle_historical_topic_id": decision.get("historical_topic_id"),
            "lifecycle_confidence": _clamp(decision.get("confidence")),
            "lifecycle_reason": str(decision.get("reason") or "").strip(),
            "feedback_type_counts": _clean_counts(feedback_type_counts),
            "feature_candidate_counts": _clean_counts(feature_counts),
            "platform_counts": _clean_counts(platform_counts),
            "appversion_counts": _clean_counts(appversion_counts),
            "source_links": source_links,
            "evidence_span_count": len(evidence_spans),
            "has_media_evidence": any(bool(unit.get("has_media_evidence")) for unit in member_units),
            "confidence": _clamp(topic.get("confidence")),
            "needs_review": bool(topic.get("needs_review")) or any(bool(unit.get("needs_review")) for unit in member_units),
            "known_context": _has_known_context(member_units),
            "feature_policy": feature_policy,
            "representative_issue_units": [
                {
                    "issue_unit_id": str(unit.get("issue_unit_id") or ""),
                    "conversation_id": str(unit.get("conversation_id") or ""),
                    "summary": str(unit.get("summary") or "").strip(),
                    "feedback_type": str(unit.get("feedback_type") or ""),
                    "feature_id": str(unit.get("feature_id") or "unknown_feature"),
                    "evidence_spans": _strings(unit.get("evidence_spans") or [])[:3],
                    "evidence_links": _strings(unit.get("evidence_links") or []),
                    "platform": str(unit.get("platform") or ""),
                    "appversion": str(unit.get("appversion") or ""),
                    "has_media_evidence": bool(unit.get("has_media_evidence")),
                }
                for unit in member_units[:5]
            ],
        })

    expected_ids = {str(topic.get("daily_topic_id") or "") for topic in report_topics}
    validate_feature_coverage(features, expected_ids)
    return sorted(features, key=lambda row: row["daily_topic_id"])


def validate_feature_coverage(
    features: list[dict[str, Any]],
    expected_daily_topic_ids: set[str],
) -> None:
    actual_ids = [str(row.get("daily_topic_id") or "") for row in features]
    if (
        not all(actual_ids)
        or len(actual_ids) != len(set(actual_ids))
        or set(actual_ids) != expected_daily_topic_ids
    ):
        raise ValueError("topic signal features must provide exact coverage")


def _resolve_feature_policy(
    member_units: list[dict[str, Any]],
    catalog: FeatureCatalog,
) -> dict[str, Any]:
    feature_ids = _dedupe(
        str(unit.get("feature_id") or "unknown_feature")
        for unit in member_units
    )
    known_features = [catalog.get(feature_id) for feature_id in feature_ids]
    known_features = [feature for feature in known_features if feature is not None]
    unknown_ids = [feature_id for feature_id in feature_ids if catalog.get(feature_id) is None]
    policies = sorted({feature.report_policy for feature in known_features if feature.report_policy})
    if known_features and not unknown_ids and all(
        feature.report_policy == "不进入报告" for feature in known_features
    ):
        action = "exclude"
    elif any(feature.report_policy == "仅人工复核" for feature in known_features):
        action = "manual_review"
    elif known_features and not unknown_ids and all(
        feature.report_policy == "低优先级观察" for feature in known_features
    ):
        action = "observe"
    elif any(feature.report_policy == "进入报告" for feature in known_features):
        action = "eligible"
    else:
        action = "review"
    return {
        "action": action,
        "known_feature_ids": [feature.feature_id for feature in known_features],
        "unknown_feature_ids": unknown_ids,
        "policies": policies,
        "known": len(known_features),
        "unknown": len(unknown_ids),
    }


def _has_known_context(member_units: list[dict[str, Any]]) -> bool:
    return any(
        str(unit.get("platform") or "").strip()
        or str(unit.get("appversion") or "").strip()
        or str(unit.get("feature_id") or "unknown_feature") != "unknown_feature"
        for unit in member_units
    )


def _unique_map(
    rows: Iterable[dict[str, Any]],
    key_name: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(key_name) or "")
        if not key:
            raise ValueError(f"{label} {key_name} is required")
        if key in result:
            raise ValueError(f"duplicate {label} {key_name}: {key}")
        result[key] = row
    return result


def _clean_counts(values: Counter[str]) -> dict[str, int]:
    return dict(sorted((key, count) for key, count in values.items() if key))


def _strings(values: Iterable[Any]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
