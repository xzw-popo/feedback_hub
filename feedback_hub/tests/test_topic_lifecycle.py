from __future__ import annotations

import pytest

from feedback_hub.topic_discovery.lifecycle import (
    apply_topic_events,
    match_daily_topics,
    seed_topic_store,
)


def _daily(topic_id: str, title: str = "语音输入不上屏") -> dict:
    return {
        "daily_topic_id": topic_id,
        "source_date": "2026-07-12",
        "title": title,
        "description": "语音结束后没有文字结果",
        "member_issue_unit_ids": [f"{topic_id}:issue"],
        "conversation_ids": [f"{topic_id}:conversation"],
        "conversation_count": 1,
        "evidence_links": ["https://example.test/chat"],
        "confidence": 0.8,
        "needs_review": False,
    }


def test_seed_topic_store_creates_provisional_topics_and_events() -> None:
    daily_topics = [
        _daily("daily:2026-07-12:0002", "候选词显示异常"),
        _daily("daily:2026-07-12:0001", "语音输入不上屏"),
    ]

    topics, events = seed_topic_store(daily_topics, "2026-07-12")

    assert [topic["topic_id"] for topic in topics] == ["topic:000001", "topic:000002"]
    assert topics[0]["canonical_title"] == "语音输入不上屏"
    assert topics[0]["topic_version"] == 1
    assert topics[0]["status"] == "provisional"
    assert topics[0]["first_seen"] == "2026-07-12"
    assert topics[0]["last_seen"] == "2026-07-12"
    assert topics[0]["source_daily_topic_ids"] == ["daily:2026-07-12:0001"]
    assert topics[0]["representative_summaries"] == ["语音输入不上屏"]
    assert topics[0]["total_issue_unit_count"] == 0
    assert topics[0]["total_conversation_count"] == 1
    assert [event["event_type"] for event in events] == ["topic_created", "topic_created"]
    assert events[0]["topic_id"] == "topic:000001"


def test_match_daily_topics_requires_explicit_decisions_with_history() -> None:
    historical, _events = seed_topic_store([_daily("daily:2026-07-11:0001")], "2026-07-11")

    with pytest.raises(ValueError, match="explicit decisions"):
        match_daily_topics([_daily("daily:2026-07-12:0001")], historical)


def test_match_daily_topics_requires_explicit_decisions_with_empty_history() -> None:
    with pytest.raises(ValueError, match="explicit decisions"):
        match_daily_topics([_daily("daily:2026-07-12:0001")], [])


def test_empty_history_applies_new_and_low_information_decisions() -> None:
    daily_topics = [
        _daily("daily:2026-07-12:0001", "明确的新需求"),
        _daily("daily:2026-07-12:0002", "无法使用"),
    ]
    decisions = [
        {
            "daily_topic_id": "daily:2026-07-12:0001",
            "verdict": "new_topic",
            "historical_topic_id": None,
        },
        {
            "daily_topic_id": "daily:2026-07-12:0002",
            "verdict": "low_information",
            "historical_topic_id": None,
        },
    ]

    events = match_daily_topics(daily_topics, [], decisions)
    store = apply_topic_events([], daily_topics, events)

    assert [event["event_type"] for event in events] == [
        "topic_created",
        "low_information_held",
    ]
    assert [event["topic_id"] for event in events] == ["topic:000001", None]
    assert [topic["topic_id"] for topic in store] == ["topic:000001"]


def test_empty_history_rejects_verdict_that_requires_history() -> None:
    with pytest.raises(ValueError, match="unknown historical_topic_id"):
        match_daily_topics(
            [_daily("daily:2026-07-12:0001")],
            [],
            decisions=[{
                "daily_topic_id": "daily:2026-07-12:0001",
                "verdict": "same_topic",
                "historical_topic_id": "topic:000001",
            }],
        )


def test_match_daily_topics_maps_all_supported_decisions() -> None:
    historical, _events = seed_topic_store([_daily("daily:2026-07-11:0001")], "2026-07-11")
    daily_topics = [
        _daily("daily:2026-07-12:0001"),
        _daily("daily:2026-07-12:0002", "新需求"),
        _daily("daily:2026-07-12:0003", "相关新症状"),
        _daily("daily:2026-07-12:0004", "证据不足"),
    ]
    decisions = [
        {
            "daily_topic_id": "daily:2026-07-12:0001",
            "verdict": "same_topic",
            "historical_topic_id": "topic:000001",
        },
        {"daily_topic_id": "daily:2026-07-12:0002", "verdict": "new_topic"},
        {
            "daily_topic_id": "daily:2026-07-12:0003",
            "verdict": "possible_subtopic",
            "historical_topic_id": "topic:000001",
        },
        {
            "daily_topic_id": "daily:2026-07-12:0004",
            "verdict": "uncertain",
            "historical_topic_id": "topic:000001",
        },
    ]

    events = match_daily_topics(daily_topics, historical, decisions=decisions)

    assert [event["event_type"] for event in events] == [
        "topic_extended",
        "topic_created",
        "possible_subtopic",
        "match_uncertain",
    ]
    assert [event["topic_id"] for event in events] == [
        "topic:000001",
        "topic:000002",
        "topic:000003",
        "topic:000004",
    ]
    assert events[2]["related_topic_id"] == "topic:000001"


def test_match_daily_topics_rejects_duplicate_or_missing_decisions() -> None:
    historical, _events = seed_topic_store([_daily("daily:2026-07-11:0001")], "2026-07-11")
    daily_topics = [_daily("daily:2026-07-12:0001"), _daily("daily:2026-07-12:0002")]

    with pytest.raises(ValueError, match="duplicate.*missing"):
        match_daily_topics(
            daily_topics,
            historical,
            decisions=[
                {"daily_topic_id": "daily:2026-07-12:0001", "verdict": "new_topic"},
                {"daily_topic_id": "daily:2026-07-12:0001", "verdict": "new_topic"},
            ],
        )


def test_apply_topic_events_extends_existing_and_creates_child_topics() -> None:
    historical, _events = seed_topic_store([_daily("daily:2026-07-11:0001")], "2026-07-11")
    daily_topics = [
        _daily("daily:2026-07-12:0001"),
        _daily("daily:2026-07-12:0002", "语音输入黑屏"),
    ]
    events = match_daily_topics(
        daily_topics,
        historical,
        decisions=[
            {
                "daily_topic_id": "daily:2026-07-12:0001",
                "verdict": "same_topic",
                "historical_topic_id": "topic:000001",
            },
            {
                "daily_topic_id": "daily:2026-07-12:0002",
                "verdict": "possible_subtopic",
                "historical_topic_id": "topic:000001",
            },
        ],
    )

    topics = apply_topic_events(historical, daily_topics, events)

    assert topics[0]["topic_id"] == "topic:000001"
    assert topics[0]["topic_version"] == 2
    assert topics[0]["last_seen"] == "2026-07-12"
    assert topics[0]["source_daily_topic_ids"] == [
        "daily:2026-07-11:0001",
        "daily:2026-07-12:0001",
    ]
    assert topics[0]["representative_summaries"] == ["语音输入不上屏"]
    assert topics[0]["total_conversation_count"] == 2
    assert topics[1]["topic_id"] == "topic:000002"
    assert topics[1]["parent_topic_ids"] == ["topic:000001"]
    assert topics[1]["status"] == "provisional"


def test_low_information_event_does_not_allocate_or_persist_topic() -> None:
    historical, _events = seed_topic_store(
        [_daily("daily:2026-07-11:0001")],
        "2026-07-11",
    )
    held = _daily("daily:2026-07-12:0001", "无法使用")

    events = match_daily_topics(
        [held],
        historical,
        decisions=[{
            "daily_topic_id": held["daily_topic_id"],
            "verdict": "low_information",
            "historical_topic_id": None,
            "confidence": 0.91,
            "reason": "对象与症状不明确",
        }],
    )

    assert events[0]["event_type"] == "low_information_held"
    assert events[0]["topic_id"] is None
    assert events[0]["related_topic_id"] is None
    assert apply_topic_events(historical, [held], events) == historical
