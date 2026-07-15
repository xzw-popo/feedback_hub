"""Versioned lifecycle transitions for dynamically discovered topics."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


_VERDICT_TO_EVENT = {
    "same_topic": "topic_extended",
    "new_topic": "topic_created",
    "possible_subtopic": "possible_subtopic",
    "uncertain": "match_uncertain",
    "low_information": "low_information_held",
}


def seed_topic_store(
    daily_topics: list[dict[str, Any]],
    source_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create a first-day provisional store without historical claims."""
    ordered = sorted(daily_topics, key=lambda topic: str(topic.get("daily_topic_id") or ""))
    topics: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for index, daily_topic in enumerate(ordered, 1):
        topic_id = f"topic:{index:06d}"
        topics.append(_new_topic(topic_id, daily_topic, source_date, parent_topic_ids=[]))
        events.append({
            "event_id": f"event:{source_date}:{index:04d}",
            "event_type": "topic_created",
            "source_date": source_date,
            "daily_topic_id": str(daily_topic.get("daily_topic_id") or ""),
            "topic_id": topic_id,
            "related_topic_id": None,
            "reason": "first_day_topic_store_seed",
        })
    return topics, events


def match_daily_topics(
    daily_topics: list[dict[str, Any]],
    historical_topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Translate explicit model decisions into auditable lifecycle events."""
    ordered_daily = sorted(daily_topics, key=lambda topic: str(topic.get("daily_topic_id") or ""))
    if not historical_topics:
        _topics, events = seed_topic_store(
            ordered_daily,
            str(ordered_daily[0].get("source_date") or "") if ordered_daily else "",
        )
        return events
    if decisions is None:
        raise ValueError("explicit decisions are required when historical topics exist")

    daily_ids = [str(topic.get("daily_topic_id") or "") for topic in ordered_daily]
    decision_ids = [str(decision.get("daily_topic_id") or "") for decision in decisions]
    duplicates = sorted({value for value in decision_ids if decision_ids.count(value) > 1})
    missing = sorted(set(daily_ids) - set(decision_ids))
    unknown = sorted(set(decision_ids) - set(daily_ids))
    if duplicates or missing or unknown or len(decision_ids) != len(daily_ids):
        raise ValueError(
            f"invalid decisions: duplicates={duplicates}, missing={missing}, unknown={unknown}"
        )
    decision_by_daily = {str(decision["daily_topic_id"]): decision for decision in decisions}
    historical_ids = {str(topic.get("topic_id") or "") for topic in historical_topics}
    next_topic_number = _max_topic_number(historical_topics) + 1
    events: list[dict[str, Any]] = []
    for index, daily_topic in enumerate(ordered_daily, 1):
        daily_id = str(daily_topic.get("daily_topic_id") or "")
        source_date = str(daily_topic.get("source_date") or "")
        decision = decision_by_daily[daily_id]
        verdict = str(decision.get("verdict") or "")
        if verdict not in _VERDICT_TO_EVENT:
            raise ValueError(f"unsupported topic verdict: {verdict}")
        related_topic_id = str(decision.get("historical_topic_id") or "") or None
        if verdict in {"same_topic", "possible_subtopic", "uncertain"}:
            if related_topic_id not in historical_ids:
                raise ValueError(f"unknown historical_topic_id: {related_topic_id}")
        if verdict == "low_information" and related_topic_id is not None:
            raise ValueError("low_information must not reference a historical topic")
        if verdict == "same_topic":
            topic_id = str(related_topic_id)
        elif verdict == "low_information":
            topic_id = None
        else:
            topic_id = f"topic:{next_topic_number:06d}"
            next_topic_number += 1
        events.append({
            "event_id": f"event:{source_date}:{index:04d}",
            "event_type": _VERDICT_TO_EVENT[verdict],
            "source_date": source_date,
            "daily_topic_id": daily_id,
            "topic_id": topic_id,
            "related_topic_id": (
                related_topic_id
                if verdict not in {"new_topic", "low_information"}
                else None
            ),
            "reason": str(decision.get("reason") or verdict),
        })
    return events


def apply_topic_events(
    historical_topics: list[dict[str, Any]],
    daily_topics: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply lifecycle events while preserving stable topic identities."""
    topics = {str(topic.get("topic_id") or ""): deepcopy(topic) for topic in historical_topics}
    daily_by_id = {
        str(topic.get("daily_topic_id") or ""): topic
        for topic in daily_topics
    }
    for event in events:
        event_type = str(event.get("event_type") or "")
        daily_id = str(event.get("daily_topic_id") or "")
        topic_id = str(event.get("topic_id") or "")
        daily_topic = daily_by_id.get(daily_id)
        if daily_topic is None:
            raise ValueError(f"unknown daily_topic_id in event: {daily_id}")
        if event_type == "low_information_held":
            continue
        source_date = str(event.get("source_date") or daily_topic.get("source_date") or "")
        if event_type == "topic_extended":
            if topic_id not in topics:
                raise ValueError(f"unknown topic_id in extension: {topic_id}")
            topic = topics[topic_id]
            source_ids = list(topic.get("source_daily_topic_ids") or [])
            if daily_id not in source_ids:
                source_ids.append(daily_id)
            topic["source_daily_topic_ids"] = source_ids
            topic["last_seen"] = source_date
            topic["topic_version"] = int(topic.get("topic_version") or 1) + 1
            topic["latest_daily_topic_id"] = daily_id
            topic["total_issue_unit_count"] = int(topic.get("total_issue_unit_count") or 0) + int(
                daily_topic.get("issue_unit_count") or 0
            )
            topic["total_conversation_count"] = int(topic.get("total_conversation_count") or 0) + int(
                daily_topic.get("conversation_count") or 0
            )
            topic["representative_summaries"] = _dedupe_strings([
                *(topic.get("representative_summaries") or []),
                *_representative_summaries(daily_topic),
            ])[:5]
            continue
        if event_type not in {"topic_created", "possible_subtopic", "match_uncertain"}:
            raise ValueError(f"unsupported topic event: {event_type}")
        if topic_id in topics:
            raise ValueError(f"duplicate topic_id in creation event: {topic_id}")
        related = str(event.get("related_topic_id") or "")
        parent_ids = [related] if event_type == "possible_subtopic" and related else []
        topics[topic_id] = _new_topic(topic_id, daily_topic, source_date, parent_topic_ids=parent_ids)
        if event_type == "match_uncertain":
            topics[topic_id]["match_status"] = "uncertain"
            topics[topic_id]["related_topic_ids"] = [related] if related else []
    return [topics[topic_id] for topic_id in sorted(topics, key=_topic_sort_key)]


def _new_topic(
    topic_id: str,
    daily_topic: dict[str, Any],
    source_date: str,
    *,
    parent_topic_ids: list[str],
) -> dict[str, Any]:
    return {
        "topic_id": topic_id,
        "topic_version": 1,
        "status": "provisional",
        "canonical_title": str(daily_topic.get("title") or "").strip(),
        "canonical_description": str(daily_topic.get("description") or "").strip(),
        "first_seen": source_date,
        "last_seen": source_date,
        "source_daily_topic_ids": [str(daily_topic.get("daily_topic_id") or "")],
        "latest_daily_topic_id": str(daily_topic.get("daily_topic_id") or ""),
        "representative_summaries": _representative_summaries(daily_topic),
        "total_issue_unit_count": int(daily_topic.get("issue_unit_count") or 0),
        "total_conversation_count": int(daily_topic.get("conversation_count") or 0),
        "parent_topic_ids": list(parent_topic_ids),
        "merged_into_topic_id": None,
    }


def _max_topic_number(topics: list[dict[str, Any]]) -> int:
    values = []
    for topic in topics:
        match = re.fullmatch(r"topic:(\d+)", str(topic.get("topic_id") or ""))
        if match:
            values.append(int(match.group(1)))
    return max(values, default=0)


def _topic_sort_key(topic_id: str) -> tuple[int, str]:
    match = re.fullmatch(r"topic:(\d+)", topic_id)
    return (int(match.group(1)), topic_id) if match else (10**12, topic_id)


def _representative_summaries(daily_topic: dict[str, Any]) -> list[str]:
    summaries = [
        str(member.get("summary") or "").strip()
        for member in daily_topic.get("members") or []
        if isinstance(member, dict)
    ]
    title = str(daily_topic.get("title") or "").strip()
    return _dedupe_strings(summaries or [title])[:5]


def _dedupe_strings(values: list[Any]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output
