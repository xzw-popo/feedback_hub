from __future__ import annotations

import json

import numpy as np
import pytest

from feedback_hub.topic_discovery.lifecycle_matching import (
    aggregate_topic_embeddings,
    build_lifecycle_candidate_plan,
    build_lifecycle_match_prompt,
    parse_lifecycle_match_reply,
    run_lifecycle_batches_multi_channel,
)
from feedback_hub.topic_discovery.model_routes import QuotaExhaustedError


def _daily(topic_id: str, title: str) -> dict:
    return {
        "daily_topic_id": topic_id,
        "title": title,
        "description": title,
        "issue_unit_count": 2,
        "conversation_count": 2,
        "members": [{"summary": title}],
        "platform_counts": {"Android": 2},
    }


def _historical(topic_id: str, title: str) -> dict:
    return {
        "topic_id": topic_id,
        "canonical_title": title,
        "canonical_description": title,
        "first_seen": "2026-07-12",
        "last_seen": "2026-07-12",
        "status": "provisional",
    }


def test_aggregate_topic_embeddings_normalizes_member_mean() -> None:
    topics = [{"member_issue_unit_ids": ["i1", "i2"]}, {"member_issue_unit_ids": ["i3"]}]
    issue_ids = ["i1", "i2", "i3"]
    issue_embeddings = np.asarray([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]], dtype="float32")

    result = aggregate_topic_embeddings(topics, issue_ids, issue_embeddings)

    assert result.shape == (2, 2)
    assert result[0] == pytest.approx([2**-0.5, 2**-0.5])
    assert result[1] == pytest.approx([-1.0, 0.0])


def test_build_lifecycle_candidate_plan_keeps_top_matches_and_no_match_topics() -> None:
    daily = [_daily("d1", "语音不上屏"), _daily("d2", "新增日语键盘")]
    historical = [_historical("t1", "语音结果不上屏"), _historical("t2", "剪贴板同步失败")]
    daily_embeddings = np.asarray([[1.0, 0.0], [0.0, -1.0]], dtype="float32")
    historical_embeddings = np.asarray([[0.9, 0.4358899], [0.0, 1.0]], dtype="float32")

    plan = build_lifecycle_candidate_plan(
        daily,
        historical,
        daily_embeddings,
        historical_embeddings,
        threshold=0.5,
        top_k=2,
        max_batch_size=10,
    )

    assert plan["candidate_rows"][0]["daily_topic_id"] == "d1"
    assert plan["candidate_rows"][0]["candidates"][0]["topic_id"] == "t1"
    assert plan["candidate_rows"][0]["candidates"][0]["cosine_similarity"] == pytest.approx(0.9)
    assert plan["candidate_rows"][1]["daily_topic_id"] == "d2"
    assert plan["candidate_rows"][1]["candidates"] == []
    assert len(plan["batches"]) == 1
    assert [item["daily_topic_id"] for item in plan["batches"][0]["items"]] == ["d1", "d2"]
    assert plan["stats"]["daily_topics_without_candidates"] == 1
    assert plan["stats"]["model_decision_topics"] == 2
    assert plan["stats"]["deterministic_new_topics"] == 0


def test_build_lifecycle_candidate_plan_batches_empty_history_for_eligibility() -> None:
    daily = [_daily("d1", "明确需求"), _daily("d2", "无法使用")]
    daily_embeddings = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
    historical_embeddings = np.empty((0, 2), dtype="float32")

    plan = build_lifecycle_candidate_plan(
        daily,
        [],
        daily_embeddings,
        historical_embeddings,
    )

    assert len(plan["candidate_rows"]) == 2
    assert all(not row["candidates"] for row in plan["candidate_rows"])
    assert [item["daily_topic_id"] for item in plan["batches"][0]["items"]] == ["d1", "d2"]
    assert plan["stats"]["model_decision_topics"] == 2


def test_lifecycle_prompt_treats_history_as_memory_not_fixed_taxonomy() -> None:
    batch = {
        "batch_id": "match:0001",
        "items": [{
            "daily_topic": _daily("d1", "语音不上屏"),
            "candidates": [{
                **_historical("t1", "语音结果不上屏"),
                "cosine_similarity": 0.9,
            }],
        }],
    }

    prompt = build_lifecycle_match_prompt(batch)

    assert "memory, not a fixed taxonomy" in prompt
    assert "same_topic" in prompt
    assert "possible_subtopic" in prompt
    assert "new_topic" in prompt
    assert "low_information" in prompt
    assert "same product object or capability" in prompt
    assert "sorting, sizing, switches, entry points" in prompt
    assert "Bugs are platform- or mechanism-specific by default" in prompt
    assert "Feature requests may match across platforms" in prompt
    assert "d1" in prompt
    assert "t1" in prompt
    assert "Return exactly one legal JSON object" in prompt
    assert "ASCII double quotes inside reason" in prompt
    assert "Chinese corner quotes" in prompt
    assert "Return shape:\n{" in prompt


def test_parse_lifecycle_match_reply_requires_exact_daily_coverage_and_valid_history() -> None:
    reply = json.dumps({
        "decisions": [{
            "daily_topic_id": "d1",
            "verdict": "same_topic",
            "historical_topic_id": "t1",
            "confidence": 0.9,
            "reason": "同一问题",
        }, {
            "daily_topic_id": "d2",
            "verdict": "new_topic",
            "historical_topic_id": None,
            "confidence": 0.8,
            "reason": "历史无对应主题",
        }]
    }, ensure_ascii=False)

    decisions = parse_lifecycle_match_reply(
        reply,
        allowed_daily_topic_ids={"d1", "d2"},
        allowed_historical_topic_ids={"t1"},
    )

    assert [decision["verdict"] for decision in decisions] == ["same_topic", "new_topic"]
    assert decisions[0]["historical_topic_id"] == "t1"
    assert decisions[1]["historical_topic_id"] is None

    low_information = parse_lifecycle_match_reply(
        json.dumps({
            "decisions": [{
                "daily_topic_id": "d1",
                "verdict": "low_information",
                "historical_topic_id": None,
                "confidence": 0.9,
                "reason": "对象与症状均不明确",
            }]
        }, ensure_ascii=False),
        allowed_daily_topic_ids={"d1"},
        allowed_historical_topic_ids={"t1"},
    )
    assert low_information[0]["verdict"] == "low_information"
    assert low_information[0]["historical_topic_id"] is None

    with pytest.raises(ValueError, match="low_information"):
        parse_lifecycle_match_reply(
            json.dumps({
                "decisions": [{
                    "daily_topic_id": "d1",
                    "verdict": "low_information",
                    "historical_topic_id": "t1",
                    "confidence": 0.9,
                    "reason": "对象与症状均不明确",
                }]
            }, ensure_ascii=False),
            allowed_daily_topic_ids={"d1"},
            allowed_historical_topic_ids={"t1"},
        )

    with pytest.raises(ValueError, match="coverage"):
        parse_lifecycle_match_reply(
            '{"decisions":[{"daily_topic_id":"d1","verdict":"new_topic"}]}',
            allowed_daily_topic_ids={"d1", "d2"},
            allowed_historical_topic_ids={"t1"},
        )


def test_run_lifecycle_batches_multi_channel_balances_routes(tmp_path) -> None:
    batches = [{
        "batch_id": f"match:{index:04d}",
        "items": [{
            "daily_topic_id": f"d{index}",
            "daily_topic": _daily(f"d{index}", "新主题"),
            "candidates": [],
        }],
    } for index in range(1, 5)]
    routes = [
        {"name": "a", "api_url": "https://a.test", "token": "secret-a"},
        {"name": "b", "api_url": "https://b.test", "token": "secret-b"},
    ]

    def fake_call(prompt, **kwargs):
        batch_items = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        return json.dumps({
            "decisions": [{
                "daily_topic_id": item["daily_topic_id"],
                "verdict": "new_topic",
                "historical_topic_id": None,
                "confidence": 0.9,
                "reason": "无历史候选",
            } for item in batch_items]
        }, ensure_ascii=False)

    rows, stats = run_lifecycle_batches_multi_channel(
        batches,
        routes=routes,
        output_path=tmp_path / "matches.jsonl",
        concurrency_per_route=1,
        call_fn=fake_call,
    )

    assert len(rows) == 4
    assert stats["route_counts"] == {"a": 2, "b": 2}
    assert stats["decisions"] == 4
    assert stats["call_failed"] == 0
    assert stats["parse_failed"] == 0
    assert {row["route_source"] for row in rows} == {"a", "b"}

    with pytest.raises(ValueError, match="historical"):
        parse_lifecycle_match_reply(
            '{"decisions":[{"daily_topic_id":"d1","verdict":"same_topic","historical_topic_id":"bad"}]}',
            allowed_daily_topic_ids={"d1"},
            allowed_historical_topic_ids={"t1"},
        )


def test_run_lifecycle_batches_multi_channel_propagates_quota_pause(tmp_path) -> None:
    batch = {
        "batch_id": "match:0001",
        "items": [{
            "daily_topic_id": "d1",
            "daily_topic": _daily("d1", "新主题"),
            "candidates": [],
        }],
    }

    def quota_call(prompt, **kwargs):
        raise QuotaExhaustedError(kwargs["route"].name, 429, "quota")

    output = tmp_path / "matches.jsonl"
    rows, stats = run_lifecycle_batches_multi_channel(
        [batch],
        routes=[{"name": "a", "api_url": "https://a.test", "token": "secret"}],
        output_path=output,
        concurrency_per_route=1,
        call_fn=quota_call,
    )

    assert rows == []
    assert stats["run_status"] == "paused_quota_exhausted"
    assert not output.exists()


def test_lifecycle_runner_retries_parse_then_splits_to_three(tmp_path) -> None:
    batch = {
        "batch_id": "match:0001",
        "items": [{
            "daily_topic_id": f"d{index}",
            "daily_topic": _daily(f"d{index}", "新主题"),
            "candidates": [],
        } for index in range(1, 6)],
    }
    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) <= 2:
            return '{"decisions":['
        items = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        return json.dumps({
            "decisions": [{
                "daily_topic_id": item["daily_topic_id"],
                "verdict": "new_topic",
                "historical_topic_id": None,
                "confidence": 0.9,
                "reason": "无历史候选",
            } for item in items]
        }, ensure_ascii=False)

    rows, stats = run_lifecycle_batches_multi_channel(
        [batch],
        routes=[{"name": "a", "api_url": "https://a.test", "token": "secret"}],
        output_path=tmp_path / "matches.jsonl",
        concurrency_per_route=1,
        call_fn=fake_call,
    )

    assert len(calls) == 4
    assert len(rows[0]["decisions"]) == 5
    assert rows[0]["match_parse_attempts"] == 4
    assert rows[0]["match_split_after_parse_failure"] is True
    assert stats["parse_failed"] == 0
