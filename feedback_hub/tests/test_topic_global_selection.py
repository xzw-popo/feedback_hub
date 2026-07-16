from __future__ import annotations

from collections import Counter
import importlib
import json

import pytest


def _api():
    return importlib.import_module("feedback_hub.topic_discovery.global_selection")


def _insight(
    insight_id: str,
    decision: str,
    signal_type: str,
    *,
    today: int = 1,
    baseline: list[int] | None = None,
    confidence: float = 0.8,
    needs_human_review: bool = False,
) -> dict:
    return {
        "insight_id": insight_id,
        "report_decision": decision,
        "signal_type": signal_type,
        "headline": f"洞察{insight_id}",
        "summary": "明确的用户反馈",
        "selection_reason": "局部判断",
        "trend_claim": "none",
        "source_candidate_ids": [insight_id],
        "representative_issue_unit_ids": [f"issue-{insight_id}"],
        "confidence": confidence,
        "needs_human_review": needs_human_review,
        "_fixture_today": today,
        "_fixture_baseline": list(baseline or [0] * 7),
    }


def _candidates_for(insights: list[dict]) -> dict[str, dict]:
    dates = [f"2026-07-{day:02d}" for day in range(7, 14)]
    result = {}
    for insight in insights:
        candidate_id = insight["source_candidate_ids"][0]
        today = insight["_fixture_today"]
        baseline = insight["_fixture_baseline"]
        result[candidate_id] = {
            "candidate_id": candidate_id,
            "daily_topic_id": f"daily:{candidate_id}",
            "stable_topic_id": f"topic:{candidate_id}",
            "title": insight["headline"],
            "description": insight["summary"],
            "today_conversation_ids": [f"today-{candidate_id}-{index}" for index in range(today)],
            "baseline_dates": dates,
            "baseline_conversation_ids_by_date": {
                day: [f"history-{candidate_id}-{day}-{index}" for index in range(count)]
                for day, count in zip(dates, baseline)
            },
            "source_links": [f"https://feedback/{candidate_id}"],
            "representative_issue_units": [{
                "issue_unit_id": f"issue-{candidate_id}",
                "summary": insight["summary"],
                "evidence_spans": ["明确证据"],
            }],
            "evidence_span_count": 1,
            "known_context": True,
            "platform_counts": {"Android": today},
            "feature_candidate_counts": {"voice_input": today},
            "appversion_counts": {},
            "recall_reasons": ["high_value_single"],
        }
    return result


def _lane_fixture(*, repeated: int, new: int, demand: int, single: int) -> list[dict]:
    rows = []
    for signal_type, count in (
        ("rising_or_repeated_bug", repeated),
        ("new_bug", new),
        ("demand_opportunity", demand),
        ("high_value_single", single),
    ):
        for index in range(count):
            rows.append(_insight(
                f"{signal_type}-{index:02d}",
                "nominate",
                signal_type,
                today=count - index,
                baseline=[1] * 7,
            ))
    return rows


def _shortlist_row(
    insight_id: str,
    *,
    source_candidate_ids: list[str] | None = None,
    needs_human_review: bool = False,
) -> dict:
    return {
        "insight_id": insight_id,
        "report_decision": "nominate",
        "signal_type": "new_bug",
        "headline": f"洞察{insight_id}",
        "summary": "明确问题",
        "selection_reason": "局部提名",
        "trend_claim": "new_signal",
        "source_candidate_ids": list(source_candidate_ids or [f"candidate-{insight_id}"]),
        "source_links": [f"https://feedback/{insight_id}"],
        "needs_human_review": needs_human_review,
        "today_conversation_count": 2,
        "baseline_daily_counts": [0] * 7,
        "baseline_active_days": 0,
        "shortlist_lanes": ["new_bug"],
        "shortlist_rank_fields": {"today_conversation_count": 2},
    }


def _selection_reply(ids: list[str], *, ranks: list[int] | None = None) -> str:
    selected = []
    for index, insight_id in enumerate(ids):
        selected.append({
            "insight_id": insight_id,
            "rank": ranks[index] if ranks is not None else index + 1,
            "selection_reason": "相对证据更强",
            "editorial_note": "保持谨慎表达",
            "report_summary": "用户反馈明确问题，需要产品关注",
        })
    return json.dumps({
        "selected_insights": selected,
        "selection_summary": "完成全局比较",
    }, ensure_ascii=False)


def test_shortlist_uses_evidence_before_local_decision() -> None:
    api = _api()
    insights = [
        _insight("weak", "nominate", "demand_opportunity", today=1),
        _insight("strong", "observe", "demand_opportunity", today=8, baseline=[2] * 7),
    ]

    rows = api.build_global_shortlist(
        insights,
        _candidates_for(insights),
        lane_limits={"demand_opportunity": 1},
    )

    assert [row["insight_id"] for row in rows] == ["strong"]
    assert rows[0]["shortlist_lanes"] == ["demand_opportunity"]


def test_shortlist_caps_each_lane_and_total_without_composite_score() -> None:
    api = _api()
    insights = _lane_fixture(repeated=12, new=10, demand=10, single=6)

    rows = api.build_global_shortlist(insights, _candidates_for(insights))

    assert len(rows) == 30
    assert Counter(row["shortlist_lanes"][0] for row in rows) == Counter({
        "rising_or_repeated_bug": 10,
        "new_bug": 8,
        "demand_opportunity": 8,
        "high_value_single": 4,
    })
    assert all("shortlist_rank_fields" in row for row in rows)
    assert all("composite_score" not in row for row in rows)


def test_shortlist_excludes_review_linkless_and_excluded_rows() -> None:
    api = _api()
    insights = [
        _insight("review", "manual_review", "new_bug"),
        _insight("flagged", "nominate", "new_bug", needs_human_review=True),
        _insight("excluded", "exclude", "new_bug"),
        _insight("linkless", "nominate", "new_bug"),
    ]
    candidates = _candidates_for(insights)
    candidates["linkless"]["source_links"] = []

    assert api.build_global_shortlist(insights, candidates) == []


def test_shortlist_marks_skin_preferences_as_low_product_priority() -> None:
    insight = _insight("skin", "nominate", "demand_opportunity", today=8, baseline=[10] * 7)
    insight["headline"] = "用户持续请求增加键盘皮肤与外观选择"

    row = _api().build_global_shortlist([insight], _candidates_for([insight]))[0]

    assert row["report_priority"] == "low"
    assert row["report_priority_reasons"] == ["other_low_priority:skin_visual_customization"]


def test_global_prompt_requests_relative_selection_without_new_facts() -> None:
    prompt = _api().build_global_selection_prompt([_shortlist_row("i1")])

    assert "GLOBAL DAILY INSIGHT SELECTION" in prompt
    assert "select zero to five" in prompt
    assert "compare every shortlist item" in prompt
    assert "must not change headline, signal_type, or trend_claim" in prompt
    assert "fixed type quota" in prompt
    assert "Do not cite model confidence" in prompt
    assert "low report_priority" in prompt
    assert "report_summary" in prompt


def test_global_parser_accepts_ranked_subset_and_empty_day() -> None:
    api = _api()
    shortlist = [_shortlist_row("i1"), _shortlist_row("i2")]

    selected = api.parse_global_selection_reply(_selection_reply(["i2"]), shortlist)

    assert selected["selected_insights"][0]["insight_id"] == "i2"
    assert selected["selected_insights"][0]["report_summary"] == "用户反馈明确问题，需要产品关注"
    assert api.parse_global_selection_reply(
        '{"selected_insights": [], "selection_summary": "今日不推送"}',
        shortlist,
    )["selected_insights"] == []


@pytest.mark.parametrize(("reply", "error"), [
    (_selection_reply(["unknown"]), "unknown insight"),
    (_selection_reply(["i1", "i2"], ranks=[1, 3]), "consecutive"),
    (_selection_reply(["i1", "i1"]), "duplicate"),
    (_selection_reply([f"i{index}" for index in range(6)]), "zero to five"),
])
def test_global_parser_rejects_invalid_selection(reply: str, error: str) -> None:
    shortlist = [_shortlist_row(f"i{index}") for index in range(6)]

    with pytest.raises(ValueError, match=error):
        _api().parse_global_selection_reply(reply, shortlist)


def test_global_parser_rejects_review_and_overlapping_source_candidates() -> None:
    api = _api()
    with pytest.raises(ValueError, match="human review"):
        api.parse_global_selection_reply(
            _selection_reply(["i1"]),
            [_shortlist_row("i1", needs_human_review=True)],
        )

    shortlist = [
        _shortlist_row("i1", source_candidate_ids=["c1"]),
        _shortlist_row("i2", source_candidate_ids=["c1", "c2"]),
    ]
    with pytest.raises(ValueError, match="overlap"):
        api.parse_global_selection_reply(_selection_reply(["i1", "i2"]), shortlist)


def test_global_parser_rejects_model_confidence_as_report_reason() -> None:
    reply = json.loads(_selection_reply(["i1"]))
    reply["selected_insights"][0]["selection_reason"] = "模型置信度0.9，因此进入报告"

    with pytest.raises(ValueError, match="model confidence"):
        _api().parse_global_selection_reply(
            json.dumps(reply, ensure_ascii=False),
            [_shortlist_row("i1")],
        )


def test_global_selection_runner_records_route_and_parsed_result(tmp_path) -> None:
    api = _api()
    shortlist = [_shortlist_row("i1")]
    prompts = []

    def fake_call(prompt, **_kwargs):
        prompts.append(prompt)
        return _selection_reply(["i1"])

    selection, summary = api.run_global_selection_multi_channel(
        shortlist,
        routes=[{
            "name": "test",
            "endpoint_class": "openai_compatible",
            "api_url": "https://model.test",
            "token": "secret",
            "model": "glm-5.2",
        }],
        output_path=tmp_path / "global.jsonl",
        call_fn=fake_call,
    )

    assert selection["selected_insights"][0]["insight_id"] == "i1"
    assert summary["run_status"] == "completed"
    assert summary["parse_failed"] == 0
    assert summary["route_counts"] == {"test": 1}
    assert len(prompts) == 1
