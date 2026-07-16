from __future__ import annotations

from datetime import date, timedelta

import pytest

from feedback_hub.tagger.v2.feature_catalog import Feature, FeatureCatalog
from feedback_hub.topic_discovery.report_signals import (
    build_topic_signal_features,
    validate_feature_coverage,
)


def _catalog() -> FeatureCatalog:
    return FeatureCatalog([
        Feature(
            feature_id="voice_input",
            name="语音输入",
            aliases=[],
            description="语音转文字",
            platform_status={"iOS": "已存在", "Android": "已存在", "Win": "已存在", "Mac": "已存在"},
            report_policy="进入报告",
            notes="",
        ),
    ])


def _topic(topic_id: str, conversations: list[str], *, issue_ids: list[str]) -> dict:
    return {
        "daily_topic_id": topic_id,
        "title": "语音输入不上屏",
        "description": "语音识别结束后没有文字输出",
        "member_issue_unit_ids": issue_ids,
        "conversation_ids": conversations,
        "conversation_count": len(set(conversations)),
        "issue_unit_count": len(issue_ids),
        "feedback_type_counts": {"bug_problem": len(issue_ids)},
        "feature_candidate_counts": {"voice_input": len(issue_ids)},
        "platform_counts": {"Android": len(issue_ids)},
        "appversion_counts": {"3.5.0": len(issue_ids)},
        "confidence": 0.9,
        "needs_review": False,
    }


def _unit(issue_id: str, conversation_id: str, link: str) -> dict:
    return {
        "issue_unit_id": issue_id,
        "conversation_id": conversation_id,
        "summary": "语音输入结束后文字没有上屏",
        "feedback_type": "bug_problem",
        "feature_id": "voice_input",
        "evidence_spans": ["说完话后没有任何文字"],
        "evidence_links": [link],
        "has_media_evidence": False,
        "confidence": 0.9,
        "needs_review": False,
        "platform": "Android",
        "appversion": "3.5.0",
    }


def _day(day: str, topics: list[dict], decisions: list[dict], units: list[dict]) -> dict:
    return {
        "date": day,
        "daily_topics": topics,
        "lifecycle_decisions": decisions,
        "issue_units": units,
        "media_appendix": [],
    }


def _decision(daily_topic_id: str, verdict: str, historical_topic_id: str | None = None) -> dict:
    return {
        "daily_topic_id": daily_topic_id,
        "verdict": verdict,
        "historical_topic_id": historical_topic_id,
        "confidence": 0.85,
        "reason": "测试边界",
    }


def test_build_topic_signal_features_counts_unique_conversations_and_history() -> None:
    report_date = date(2026, 7, 14)
    days = []
    for offset in range(7, 1, -1):
        value = (report_date - timedelta(days=offset)).isoformat()
        days.append(_day(value, [], [], []))
    days.extend([
        _day(
            "2026-07-13",
            [_topic("daily:2026-07-13:0001", ["c1", "c1", "c2"], issue_ids=["i13a", "i13b"])],
            [_decision("daily:2026-07-13:0001", "new_topic")],
            [_unit("i13a", "c1", "https://feedback/c1"), _unit("i13b", "c2", "https://feedback/c2")],
        ),
        _day(
            "2026-07-14",
            [_topic("daily:2026-07-14:0001", ["c2", "c3"], issue_ids=["i14a", "i14b"])],
            [_decision("daily:2026-07-14:0001", "same_topic", "topic:000001")],
            [_unit("i14a", "c2", "https://feedback/c2"), _unit("i14b", "c3", "https://feedback/c3")],
        ),
    ])
    store = [{
        "topic_id": "topic:000001",
        "source_daily_topic_ids": ["daily:2026-07-13:0001", "daily:2026-07-14:0001"],
        "parent_topic_ids": [],
    }]

    rows = build_topic_signal_features(days, store, _catalog(), report_date="2026-07-14")

    assert len(rows) == 1
    assert rows[0]["today_conversation_count"] == 2
    assert rows[0]["baseline_daily_counts"] == [0, 0, 0, 0, 0, 0, 2]
    assert rows[0]["historical_active_dates"] == ["2026-07-13"]
    assert rows[0]["stable_topic_id"] == "topic:000001"
    assert rows[0]["lifecycle_verdict"] == "same_topic"
    assert rows[0]["source_links"] == ["https://feedback/c2", "https://feedback/c3"]
    assert rows[0]["evidence_span_count"] == 2
    assert rows[0]["feature_policy"]["action"] == "eligible"


def test_build_topic_signal_features_rejects_unknown_topic_members() -> None:
    days = [
        _day("2026-07-14", [_topic("d1", ["c1"], issue_ids=["missing"])], [_decision("d1", "new_topic")], []),
    ]

    with pytest.raises(ValueError, match="unknown issue members"):
        build_topic_signal_features(days, [], _catalog(), report_date="2026-07-14")


def test_validate_feature_coverage_rejects_missing_and_duplicate_topics() -> None:
    with pytest.raises(ValueError, match="exact coverage"):
        validate_feature_coverage(
            [{"daily_topic_id": "d1"}, {"daily_topic_id": "d1"}],
            {"d1", "d2"},
        )
