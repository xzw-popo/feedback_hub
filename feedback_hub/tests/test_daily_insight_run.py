from __future__ import annotations

from datetime import date
import argparse
import json
from pathlib import Path

import numpy as np
from openpyxl import Workbook

from feedback_hub.topic_discovery.daily_insight_run import (
    DailyInsightConfig,
    _normalize_legacy_local_decisions,
    run_daily_insight_experiment,
)
from scripts import run_daily_insight_signal as daily_cli
from scripts.run_daily_insight_signal import build_routes


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _topic(day: str, index: int, issue_id: str, conversations: list[str]) -> dict:
    return {
        "daily_topic_id": f"daily:{day}:{index:04d}",
        "title": f"语音问题{index}",
        "description": "语音输入后没有文字",
        "member_issue_unit_ids": [issue_id],
        "conversation_ids": conversations,
        "conversation_count": len(set(conversations)),
        "issue_unit_count": 1,
        "feedback_type_counts": {"bug_problem": 1},
        "feature_candidate_counts": {"voice_input": 1},
        "platform_counts": {"Android": 1},
        "appversion_counts": {"3.5.0": 1},
        "confidence": 0.9,
        "needs_review": False,
    }


def _unit(issue_id: str, conversation_id: str, link: str) -> dict:
    return {
        "issue_unit_id": issue_id,
        "conversation_id": conversation_id,
        "summary": "语音输入后没有文字",
        "feedback_type": "bug_problem",
        "feature_id": "voice_input",
        "evidence_spans": ["说完话没有文字"],
        "evidence_links": [link],
        "has_media_evidence": False,
        "confidence": 0.9,
        "needs_review": False,
        "platform": "Android",
        "appversion": "3.5.0",
    }


def _write_catalog(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "feature_knowledge"
    sheet.append([
        "feature_id", "功能名", "别名/用户说法", "功能说明",
        "iOS_状态", "Android_状态", "Win_状态", "Mac_状态",
        "报告处理口径", "边界说明/备注",
    ])
    sheet.append([
        "voice_input", "语音输入", "", "语音转文字",
        "已存在", "已存在", "已存在", "已存在", "进入报告", "",
    ])
    workbook.save(path)
    return path


def _write_shadow_fixture(root: Path) -> Path:
    run_root = root / "run"
    baseline = run_root / "day_20260713"
    report = run_root / "day_20260714"
    baseline_topic = _topic("2026-07-13", 1, "i13", ["history-c1"])
    report_topics = [
        _topic("2026-07-14", 1, "i14a", ["c1", "c2"]),
        _topic("2026-07-14", 2, "i14b", ["c3", "c4"]),
    ]
    _write_jsonl(baseline / "daily_topics.jsonl", [baseline_topic])
    _write_jsonl(baseline / "lifecycle_decisions.jsonl", [{
        "daily_topic_id": baseline_topic["daily_topic_id"],
        "verdict": "new_topic",
        "historical_topic_id": None,
        "confidence": 0.9,
        "reason": "首日",
    }])
    _write_jsonl(baseline / "issue_units.jsonl", [_unit("i13", "history-c1", "https://feedback/history")])
    _write_jsonl(baseline / "media_appendix.jsonl", [])

    _write_jsonl(report / "daily_topics.jsonl", report_topics)
    _write_jsonl(report / "lifecycle_decisions.jsonl", [{
        "daily_topic_id": report_topics[0]["daily_topic_id"],
        "verdict": "same_topic",
        "historical_topic_id": "topic:000001",
        "confidence": 0.9,
        "reason": "同一问题",
    }, {
        "daily_topic_id": report_topics[1]["daily_topic_id"],
        "verdict": "new_topic",
        "historical_topic_id": None,
        "confidence": 0.9,
        "reason": "新问题",
    }])
    units = [
        _unit("i14a", "c1", "https://feedback/c1"),
        _unit("i14b", "c3", "https://feedback/c3"),
    ]
    _write_jsonl(report / "issue_units.jsonl", units)
    _write_jsonl(report / "media_appendix.jsonl", [{
        "summary": "图片反馈",
        "evidence_link": "https://feedback/media",
    }])
    _write_jsonl(report / "topic_store.jsonl", [{
        "topic_id": "topic:000001",
        "source_daily_topic_ids": [baseline_topic["daily_topic_id"], report_topics[0]["daily_topic_id"]],
        "parent_topic_ids": [],
    }, {
        "topic_id": "topic:000002",
        "source_daily_topic_ids": [report_topics[1]["daily_topic_id"]],
        "parent_topic_ids": [],
    }])
    np.savez_compressed(
        report / "issue_unit_embeddings.npz",
        embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"),
    )
    return run_root


def _config(tmp_path: Path) -> DailyInsightConfig:
    return DailyInsightConfig(
        run_root=_write_shadow_fixture(tmp_path),
        report_date=date(2026, 7, 14),
        baseline_days=1,
        catalog_path=_write_catalog(tmp_path / "catalog.xlsx"),
        output_dir=tmp_path / "out",
        routes=({
            "name": "test",
            "endpoint_class": "openai_compatible",
            "api_url": "https://model.test",
            "token": "secret",
            "model": "glm-5.2",
        },),
        concurrency_per_route=1,
    )


def _fake_local_nomination_call(prompt, **_kwargs):
    items = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    return json.dumps({"insights": [{
        "report_decision": "nominate",
        "signal_type": "new_bug",
        "headline": item["title"],
        "summary": item["description"],
        "selection_reason": "进入全局比较",
        "trend_claim": "none",
        "source_candidate_ids": [item["candidate_id"]],
        "representative_issue_unit_ids": [item["representative_issue_units"][0]["issue_unit_id"]],
        "confidence": 0.8,
        "needs_human_review": False,
    } for item in items]}, ensure_ascii=False)


def _fake_two_stage_call(prompt, **kwargs):
    if "GLOBAL DAILY INSIGHT SELECTION" not in prompt:
        return _fake_local_nomination_call(prompt, **kwargs)
    items = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    return json.dumps({
        "selected_insights": [{
            "insight_id": items[0]["insight_id"],
            "rank": 1,
            "selection_reason": "相对比较后证据最明确",
            "editorial_note": "保持证据边界",
        }],
        "selection_summary": "本日选择一条",
    }, ensure_ascii=False)


def test_run_daily_insight_experiment_writes_complete_artifact_set(tmp_path) -> None:
    config = _config(tmp_path)

    summary = run_daily_insight_experiment(config, call_fn=_fake_two_stage_call)

    assert summary["run_status"] == "completed"
    assert summary["feature_topics"] == 2
    assert summary["candidate_topics"] == 2
    assert summary["shortlist_insights"] == 2
    assert summary["main_insights"] == 1
    assert (config.output_dir / "topic_signal_features.jsonl").exists()
    assert (config.output_dir / "global_shortlist.jsonl").exists()
    assert (config.output_dir / "global_selection.json").exists()
    assert (config.output_dir / "daily_insight_review.xlsx").exists()
    assert (config.output_dir / "daily_report_draft_20260714.md").exists()


def test_run_stops_before_report_when_editor_has_unresolved_failure(tmp_path) -> None:
    config = _config(tmp_path)

    summary = run_daily_insight_experiment(config, call_fn=lambda *_args, **_kwargs: "not json")

    assert summary["run_status"] == "incomplete_model_failures"
    assert not (config.output_dir / "daily_report_draft_20260714.md").exists()
    assert (config.output_dir / "run_summary.json").exists()


def test_global_selection_failure_blocks_report_without_fallback(tmp_path) -> None:
    config = _config(tmp_path)

    def call(prompt, **kwargs):
        if "GLOBAL DAILY INSIGHT SELECTION" in prompt:
            return "not json"
        return _fake_local_nomination_call(prompt, **kwargs)

    summary = run_daily_insight_experiment(config, call_fn=call)

    assert summary["run_status"] == "selection_blocked"
    assert summary["shortlist_insights"] == 2
    assert not (config.output_dir / "daily_report_draft_20260714.md").exists()
    assert not (config.output_dir / "daily_insight_review.xlsx").exists()
    assert (config.output_dir / "global_shortlist.jsonl").exists()
    assert (config.output_dir / "run_summary.json").exists()


def test_legacy_main_rows_migrate_to_nominate() -> None:
    rows = [{
        "batch_key": "legacy",
        "insights": [{"insight_id": "i1", "report_decision": "main"}],
    }]

    normalized, count = _normalize_legacy_local_decisions(rows)

    assert normalized[0]["insights"][0]["report_decision"] == "nominate"
    assert normalized[0]["insights"][0]["legacy_report_decision"] == "main"
    assert count == 1
    assert rows[0]["insights"][0]["report_decision"] == "main"


def test_cli_routes_read_credentials_only_from_named_environment(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GLM_KEY", "secret-from-env")
    args = argparse.Namespace(
        route_a_url="",
        route_a_token_env="TEST_KNOT_A",
        route_c_url="",
        route_c_token_env="TEST_KNOT_C",
        openai_url="https://model.test/v1/chat/completions",
        openai_key_env="TEST_GLM_KEY",
        openai_model="glm-5.2",
    )

    routes = build_routes(args)

    assert len(routes) == 1
    assert routes[0]["name"] == "openai_primary"
    assert routes[0]["token"] == "secret-from-env"


def test_cli_output_summary_separates_local_and_global_routes(tmp_path) -> None:
    payload = daily_cli.build_output_summary({
        "run_status": "completed",
        "report_date": "2026-07-14",
        "feature_topics": 404,
        "candidate_topics": 248,
        "shortlist_insights": 30,
        "main_insights": 4,
        "local_route_counts": {"agent_a": 59},
        "global_route_counts": {"agent_c": 1},
    }, tmp_path)

    assert payload["shortlist_insights"] == 30
    assert payload["local_route_counts"] == {"agent_a": 59}
    assert payload["global_route_counts"] == {"agent_c": 1}
    assert payload["output_dir"] == str(tmp_path)
