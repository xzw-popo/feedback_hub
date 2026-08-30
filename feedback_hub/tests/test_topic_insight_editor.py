from __future__ import annotations

import json

import numpy as np
import pytest

from feedback_hub.topic_discovery.insight_editor import (
    build_candidate_relation_plan,
    build_insight_editor_prompt,
    parse_insight_editor_reply,
    run_insight_editor_multi_channel,
)


def _candidate(
    candidate_id: str,
    daily_topic_id: str,
    *,
    stable: str | None = None,
    parent_ids: list[str] | None = None,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "daily_topic_id": daily_topic_id,
        "stable_topic_id": stable,
        "parent_topic_ids": parent_ids or [],
        "title": candidate_id,
        "description": candidate_id,
        "today_conversation_count": 1,
        "baseline_dates": ["2026-07-07"],
        "baseline_daily_counts": [0],
        "historical_active_dates": [],
        "lifecycle_verdict": "new_topic",
        "feedback_type_counts": {"bug_problem": 1},
        "feature_candidate_counts": {"voice_input": 1},
        "platform_counts": {"Android": 1},
        "appversion_counts": {"3.5.0": 1},
        "recall_reasons": ["clear_new"],
        "candidate_band": "exploratory",
        "allowed_trend_claims": ["new_signal", "none"],
        "source_links": [f"https://feedback/{candidate_id}"],
        "representative_issue_units": [{
            "issue_unit_id": f"i-{candidate_id}",
            "summary": candidate_id,
            "evidence_spans": [candidate_id],
        }],
    }


def _reply(decision: str, *, candidate_id: str = "c1", trend_claim: str = "none") -> str:
    return json.dumps({"insights": [{
        "report_decision": decision,
        "signal_type": "new_bug",
        "headline": "语音输入无文字",
        "summary": "用户说完后没有文字上屏",
        "selection_reason": "证据明确",
        "trend_claim": trend_claim,
        "source_candidate_ids": [candidate_id],
        "representative_issue_unit_ids": [f"i-{candidate_id}"],
        "confidence": 0.8,
        "needs_human_review": False,
    }]}, ensure_ascii=False)


def test_relation_plan_prioritizes_same_stable_topic_and_parent_links() -> None:
    candidates = [
        _candidate("c1", "d1", stable="t1"),
        _candidate("c2", "d2", stable="t1"),
        _candidate("c3", "d3", stable="t2", parent_ids=["t1"]),
    ]
    vectors = np.asarray([[1.0, 0.0], [0.99, 0.01], [0.8, 0.6]], dtype="float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    plan = build_candidate_relation_plan(candidates, ["d1", "d2", "d3"], vectors)

    relation_types = {row["relation_type"] for row in plan["relations"]}
    assert "same_stable_topic" in relation_types
    assert "parent_subtopic" in relation_types
    assert {item["candidate_id"] for item in plan["buckets"][0]["items"]} == {"c1", "c2", "c3"}


def test_relation_plan_never_drops_or_duplicates_candidates() -> None:
    plan = build_candidate_relation_plan(
        [_candidate("c1", "d1"), _candidate("c2", "d2")],
        ["d1", "d2"],
        np.eye(2, dtype="float32"),
    )

    ids = [item["candidate_id"] for bucket in plan["buckets"] for item in bucket["items"]]
    ids += plan["singleton_candidate_ids"]
    assert sorted(ids) == ["c1", "c2"]


def test_relation_plan_bounds_components_deterministically() -> None:
    candidates = [_candidate(f"c{index}", f"d{index}", stable="t1") for index in range(1, 6)]
    vectors = np.asarray([[1.0, index / 100] for index in range(1, 6)], dtype="float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    plan = build_candidate_relation_plan(
        candidates,
        [f"d{index}" for index in range(1, 6)],
        vectors,
        max_bucket_size=3,
    )

    assert max(len(bucket["items"]) for bucket in plan["buckets"]) <= 3
    covered = [item["candidate_id"] for bucket in plan["buckets"] for item in bucket["items"]]
    covered += plan["singleton_candidate_ids"]
    assert sorted(covered) == ["c1", "c2", "c3", "c4", "c5"]


def test_editor_prompt_separates_atomic_topics_from_report_insights() -> None:
    prompt = build_insight_editor_prompt({
        "bucket_id": "edit:0001",
        "items": [_candidate("c1", "d1")],
    })

    assert "Report grouping must not modify topic memory" in prompt
    assert "new_topic does not prove a product-level new issue" in prompt
    assert "Do not merge merely because candidates share a feature" in prompt
    assert "nominate | observe | exclude | manual_review" in prompt
    assert "nominate does not mean final report inclusion" in prompt
    assert "main | observe" not in prompt
    assert "c1" in prompt


def test_parser_rejects_unsupported_rising_claim_and_requires_exact_coverage() -> None:
    reply = json.dumps({"insights": [{
        "report_decision": "nominate",
        "signal_type": "rising_or_repeated_bug",
        "headline": "语音不上屏增加",
        "summary": "当天出现多次",
        "selection_reason": "重复反馈",
        "trend_claim": "rising",
        "source_candidate_ids": ["c1"],
        "representative_issue_unit_ids": ["i-c1"],
        "confidence": 0.9,
        "needs_human_review": False,
    }]}, ensure_ascii=False)

    with pytest.raises(ValueError, match="unsupported trend claim"):
        parse_insight_editor_reply(
            reply,
            allowed_candidate_ids={"c1"},
            allowed_trend_claims={"c1": {"none", "repeated"}},
            allowed_issue_unit_ids={"i-c1"},
        )

    with pytest.raises(ValueError, match="exact coverage"):
        parse_insight_editor_reply(
            '{"insights": []}',
            allowed_candidate_ids={"c1"},
            allowed_trend_claims={"c1": {"none"}},
            allowed_issue_unit_ids={"i-c1"},
        )


def test_parser_accepts_nominate_and_rejects_legacy_main() -> None:
    rows = parse_insight_editor_reply(
        _reply("nominate"),
        allowed_candidate_ids={"c1"},
        allowed_trend_claims={"c1": {"none"}},
        allowed_issue_unit_ids={"i-c1"},
    )

    assert rows[0]["report_decision"] == "nominate"

    with pytest.raises(ValueError, match="unsupported report decision"):
        parse_insight_editor_reply(
            _reply("main"),
            allowed_candidate_ids={"c1"},
            allowed_trend_claims={"c1": {"none"}},
            allowed_issue_unit_ids={"i-c1"},
        )


def test_nominate_requires_a_source_link(tmp_path) -> None:
    candidate = _candidate("c1", "d1")
    candidate["source_links"] = []

    rows, summary = run_insight_editor_multi_channel(
        [{"bucket_id": "edit:0001", "items": [candidate]}],
        routes=[{
            "name": "test",
            "endpoint_class": "openai_compatible",
            "api_url": "https://model.test",
            "token": "secret",
            "model": "glm-5.2",
        }],
        output_path=tmp_path / "editor.jsonl",
        concurrency_per_route=1,
        call_fn=lambda *_args, **_kwargs: _reply("nominate"),
    )

    assert summary["parse_failed"] == 1
    assert "nominate insight requires a source link" in rows[0]["editor_parse_error"]


def test_parser_accepts_report_group_and_preserves_review_fields() -> None:
    reply = json.dumps({"insights": [{
        "report_decision": "observe",
        "signal_type": "demand_opportunity",
        "headline": "补充语音方言能力",
        "summary": "两个相关需求可以共同观察",
        "selection_reason": "目标任务一致但频次较低",
        "trend_claim": "none",
        "source_candidate_ids": ["c1", "c2"],
        "representative_issue_unit_ids": ["i-c1", "i-c2"],
        "confidence": 0.82,
        "needs_human_review": True,
    }]}, ensure_ascii=False)

    rows = parse_insight_editor_reply(
        reply,
        allowed_candidate_ids={"c1", "c2"},
        allowed_trend_claims={"c1": {"none"}, "c2": {"none", "new_signal"}},
        allowed_issue_unit_ids={"i-c1", "i-c2"},
    )

    assert rows[0]["source_candidate_ids"] == ["c1", "c2"]
    assert rows[0]["needs_human_review"] is True


def test_run_insight_editor_multi_channel_balances_routes(tmp_path) -> None:
    buckets = [
        {"bucket_id": f"edit:{index:04d}", "items": [_candidate(f"c{index}", f"d{index}")]}
        for index in range(1, 5)
    ]
    routes = [
        {"name": "a", "endpoint_class": "openai_compatible", "api_url": "https://a.test", "token": "secret-a", "model": "glm-5.2"},
        {"name": "b", "endpoint_class": "knot_agent", "api_url": "https://b.test", "token": "secret-b"},
    ]

    def fake_call(prompt, **_kwargs):
        items = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        return json.dumps({"insights": [{
            "report_decision": "observe",
            "signal_type": "new_bug",
            "headline": item["title"],
            "summary": item["description"],
            "selection_reason": "单条信号先观察",
            "trend_claim": "new_signal",
            "source_candidate_ids": [item["candidate_id"]],
            "representative_issue_unit_ids": [item["representative_issue_units"][0]["issue_unit_id"]],
            "confidence": 0.8,
            "needs_human_review": False,
        } for item in items]}, ensure_ascii=False)

    rows, summary = run_insight_editor_multi_channel(
        buckets,
        routes=routes,
        output_path=tmp_path / "editor.jsonl",
        concurrency_per_route=1,
        call_fn=fake_call,
    )

    assert summary["run_status"] == "completed"
    assert summary["decisions"] == 4
    assert summary["route_counts"] == {"a": 2, "b": 2}
    assert all(row["parse_status"] == "ok" for row in rows)
