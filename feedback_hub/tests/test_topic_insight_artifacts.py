from __future__ import annotations

from openpyxl import load_workbook

from feedback_hub.topic_discovery.insight_artifacts import (
    render_daily_report,
    select_main_insights,
    write_review_workbook,
)


def _candidate(candidate_id: str, *, links: list[str] | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "daily_topic_id": f"daily:{candidate_id}",
        "stable_topic_id": f"topic:{candidate_id}",
        "title": f"候选{candidate_id}",
        "description": f"候选{candidate_id}描述",
        "today_conversation_ids": [f"conversation-{candidate_id}"],
        "today_conversation_count": 1,
        "baseline_dates": ["2026-07-07", "2026-07-08"],
        "baseline_daily_counts": [0, 1],
        "baseline_conversation_ids_by_date": {
            "2026-07-07": [],
            "2026-07-08": [f"history-{candidate_id}"],
        },
        "source_links": links if links is not None else [f"https://feedback/{candidate_id}"],
        "recall_reasons": ["high_value_single"],
        "candidate_band": "exploratory",
        "feedback_type_counts": {"bug_problem": 1},
        "feature_candidate_counts": {"voice_input": 1},
        "platform_counts": {"Android": 1},
        "appversion_counts": {"3.5.0": 1},
        "representative_issue_units": [{"issue_unit_id": f"issue-{candidate_id}", "summary": f"候选{candidate_id}"}],
    }


def _insight(
    insight_id: str,
    decision: str = "main",
    *,
    confidence: float = 0.9,
    candidate_id: str | None = None,
) -> dict:
    candidate = candidate_id or insight_id
    return {
        "insight_id": insight_id,
        "report_decision": decision,
        "signal_type": "new_bug",
        "headline": f"洞察{insight_id}",
        "summary": f"洞察{insight_id}摘要",
        "selection_reason": "证据明确",
        "trend_claim": "none",
        "source_candidate_ids": [candidate],
        "representative_issue_unit_ids": [f"issue-{candidate}"],
        "confidence": confidence,
        "needs_human_review": False,
    }


def test_select_main_insights_caps_without_filling_or_duplication() -> None:
    insights = [_insight(f"i{index}", confidence=0.9 - index / 100) for index in range(7)]
    candidates = {f"i{index}": _candidate(f"i{index}") for index in range(7)}

    selected = select_main_insights(insights, candidates, max_items=5)

    assert len(selected["main"]) == 5
    assert len(selected["observe"]) == 2
    assert len({row["insight_id"] for row in selected["main"]}) == 5
    assert all(row.get("artifact_reason") == "main_limit_overflow" for row in selected["observe"])


def test_main_insight_without_link_is_demoted_to_manual_review() -> None:
    selected = select_main_insights(
        [_insight("i1", candidate_id="c1")],
        {"c1": _candidate("c1", links=[])},
    )

    assert selected["main"] == []
    assert selected["manual_review"][0]["artifact_reason"] == "missing_source_link"


def test_grouped_insight_deduplicates_conversations() -> None:
    left = _candidate("c1")
    right = _candidate("c2")
    right["today_conversation_ids"] = left["today_conversation_ids"] + ["conversation-c2"]
    insight = _insight("i1", candidate_id="c1")
    insight["source_candidate_ids"] = ["c1", "c2"]

    selected = select_main_insights([insight], {"c1": left, "c2": right})

    assert selected["main"][0]["today_conversation_count"] == 2


def test_report_contains_clickable_sources_and_separate_media_appendix() -> None:
    selected = select_main_insights(
        [_insight("i1", candidate_id="c1")],
        {"c1": _candidate("c1", links=["https://feedback/main"])},
    )
    text = render_daily_report(
        "2026-07-14",
        selected,
        [{"summary": "图片展示异常", "evidence_link": "https://feedback/media"}],
        {"baseline_start": "2026-07-07", "baseline_end": "2026-07-13", "route_counts": {"glm": 2}},
    )

    assert "[查看原反馈](https://feedback/main)" in text
    assert "媒体附录" in text
    assert "https://feedback/media" in text
    assert "最多 5 条" not in text


def test_review_workbook_has_four_sheets_links_and_human_columns(tmp_path) -> None:
    candidate = _candidate("c1")
    selected = select_main_insights([_insight("i1", candidate_id="c1")], {"c1": candidate})
    path = tmp_path / "review.xlsx"

    write_review_workbook(
        path,
        selected=selected,
        candidates=[candidate],
        relations=[{
            "left_candidate_id": "c1",
            "right_candidate_id": "c2",
            "relation_type": "semantic_similarity",
            "cosine_similarity": 0.82,
        }],
        media_appendix=[{"summary": "图片反馈", "evidence_link": "https://feedback/media"}],
    )

    book = load_workbook(path, data_only=False)
    assert book.sheetnames == ["正文与观察", "全部候选", "原子主题关系", "媒体附录"]
    headers = [cell.value for cell in book["正文与观察"][1]]
    assert "human_report_decision" in headers
    assert "human_signal_type" in headers
    assert "human_grouping_result" in headers
    assert "human_notes" in headers
    assert any(
        cell.hyperlink and cell.hyperlink.target.startswith("https://feedback/")
        for sheet in book.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
