from __future__ import annotations

import json

import openpyxl
import pytest

from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.export import export_topic_run
from feedback_hub.topic_mining.run_store import TopicRunStore
from feedback_hub.topic_mining.service import RunVerificationError


def _spec():
    return validate_topic_spec({"schema_version": 1, "topic_name": "专题", "objective": "找工具栏", "scope": {"start_time": "2023-11-14T00:00:00+00:00", "end_time": "2023-11-16T00:00:00+00:00", "platforms": ["Win"], "products": ["微信输入法"]}, "unit": "feedback", "inclusion_criteria": ["工具栏"], "exclusion_criteria": ["系统任务栏"], "positive_examples": ["全屏工具栏"], "negative_examples": ["黑屏"], "lexical_hints": {"objects": ["工具栏"]}, "classification_labels": [{"id": "matched", "meaning": "命中"}, {"id": "not_matched", "meaning": "不命中"}], "output": {"preferred_format": "xlsx", "required_fields": ["feedback_text"]}})


def _row(item_id: str = "f-1") -> dict:
    return {
        "item_id": item_id, "label": "matched", "confidence": 0.9,
        "evidence": ["工具栏一直显示"], "reason": "全屏仍显示", "source": "classifier",
        "source_item": {
            "item_id": item_id, "feedback_id": item_id, "conversation_id": "c-1",
            "ts_ms": 1_700_000_000_000, "platform": "Win", "appversion": "1.2",
            "channel": "pc", "device_name": "PC", "text": "游戏全屏工具栏一直显示",
            "source_url": "https://example.test/chat/1",
        },
    }


def test_export_has_unique_ids_links_and_evidence(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    (artifact_dir / "final_reviewed.jsonl").write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    store.update_status(run["run_id"], "verified", stage="verified")
    path = export_topic_run(run["run_id"], "xlsx", store=store)
    wb = openpyxl.load_workbook(path, read_only=False, data_only=False)
    ws = wb["反馈清单"]
    assert [cell.value for cell in ws[1]][:6] == ["命中分类", "反馈时间", "反馈原文", "对应链接", "判定理由", "证据"]
    assert ws["D2"].hyperlink.target.startswith("https://")
    assert (artifact_dir / "final_results.jsonl").exists()
    assert (artifact_dir / "quality_report.json").exists()


@pytest.mark.parametrize("text", ["=HYPERLINK(\"bad\")", "+1+1", "-1+1", "@SUM(A1)"])
def test_export_escapes_formula_like_user_text(tmp_path, text):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    row = _row()
    row["source_item"]["text"] = text
    row["evidence"] = [text]
    (artifact_dir / "final_reviewed.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    store.update_status(run["run_id"], "verified", stage="verified")
    path = export_topic_run(run["run_id"], "xlsx", store=store)
    wb = openpyxl.load_workbook(path, data_only=False)
    assert wb["反馈清单"]["C2"].value.startswith("'")


def test_export_blocks_unverified_run(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    with pytest.raises(RunVerificationError, match="run_not_verified"):
        export_topic_run(run["run_id"], "jsonl", store=store)
