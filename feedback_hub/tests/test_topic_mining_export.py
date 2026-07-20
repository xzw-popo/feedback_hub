from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest
import hashlib

from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.export import export_topic_run
from feedback_hub.topic_mining.run_store import TopicRunStore
from feedback_hub.topic_mining.service import RunVerificationError

CUTOFF_MS = 1_700_000_000_000


def _spec():
    return validate_topic_spec({"schema_version": 1, "topic_name": "专题", "objective": "找工具栏", "scope": {"start_time": "2023-11-14T00:00:00+00:00", "end_time": "2023-11-16T00:00:00+00:00", "platforms": ["Win"], "products": ["微信输入法"]}, "unit": "feedback", "inclusion_criteria": ["工具栏"], "exclusion_criteria": ["系统任务栏"], "positive_examples": ["全屏工具栏"], "negative_examples": ["黑屏"], "lexical_hints": {"objects": ["工具栏"]}, "classification_labels": [{"id": "matched", "meaning": "命中"}, {"id": "not_matched", "meaning": "不命中"}], "output": {"preferred_format": "xlsx", "required_fields": ["feedback_text"]}})


def _row(run_id: str, item_id: str = "f-1") -> dict:
    return {
        "item_id": item_id, "label": "matched", "confidence": 0.9,
        "evidence": ["工具栏一直显示"], "reason": "全屏仍显示", "source": "classifier",
        "run_id": run_id, "data_cutoff_ms": CUTOFF_MS,
        "source_item": {
            "item_id": item_id, "feedback_id": item_id, "conversation_id": "c-1",
            "ts_ms": CUTOFF_MS, "platform": "Win", "appversion": "1.2",
            "channel": "pc", "device_name": "PC", "text": "游戏全屏工具栏一直显示",
            "source_url": "https://example.test/chat/1",
        },
    }


def test_export_has_unique_ids_links_and_evidence(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), CUTOFF_MS)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    (artifact_dir / "final_reviewed.jsonl").write_text(json.dumps(_row(run["run_id"])) + "\n", encoding="utf-8")
    store.update_status(run["run_id"], "verified", stage="verified")
    manifest = {"artifacts": {"final_reviewed.jsonl": hashlib.sha256((artifact_dir / "final_reviewed.jsonl").read_bytes()).hexdigest()}}
    store.update_manifest(run["run_id"], manifest, stage="verified", status="verified"); (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    path = export_topic_run(run["run_id"], "xlsx", store=store)
    wb = openpyxl.load_workbook(path, read_only=False, data_only=False)
    ws = wb["反馈清单"]
    assert [cell.value for cell in ws[1]][:6] == ["命中分类", "反馈时间", "反馈原文", "对应链接", "判定理由", "证据"]
    assert ws["D2"].hyperlink.target.startswith("https://")
    assert (artifact_dir / "final_results.jsonl").exists()
    assert (artifact_dir / "quality_report.json").exists()
    headers = [cell.value for cell in ws[1]]
    assert "数据截止时间" in headers
    assert ws.cell(2, headers.index("数据截止时间") + 1).value == "2023-11-14T22:13:20+00:00"
    final_rows = [json.loads(line) for line in (artifact_dir / "final_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["data_cutoff_ms"] for row in final_rows] == [CUTOFF_MS]
    report = json.loads((artifact_dir / "quality_report.json").read_text(encoding="utf-8"))
    assert report["data_cutoff_ms"] == CUTOFF_MS


@pytest.mark.parametrize("cutoff", [None, True, CUTOFF_MS - 1, str(CUTOFF_MS)])
def test_export_rejects_missing_invalid_or_mismatched_data_cutoff(tmp_path, cutoff):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), CUTOFF_MS)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    row = _row(run["run_id"])
    if cutoff is None:
        row.pop("data_cutoff_ms")
    else:
        row["data_cutoff_ms"] = cutoff
    final = artifact_dir / "final_reviewed.jsonl"
    final.write_text(json.dumps(row) + "\n", encoding="utf-8")
    manifest = {"artifacts": {final.name: hashlib.sha256(final.read_bytes()).hexdigest()}}
    store.update_manifest(run["run_id"], manifest, stage="verified", status="verified")
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RunVerificationError, match="data_cutoff"):
        export_topic_run(run["run_id"], "jsonl", store=store)


def test_export_rejects_item_newer_than_advertised_cutoff(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), CUTOFF_MS)
    artifact_dir = Path(run["artifact_dir"])
    row = _row(run["run_id"])
    row["source_item"]["ts_ms"] = CUTOFF_MS + 1
    final = artifact_dir / "final_reviewed.jsonl"
    final.write_text(json.dumps(row) + "\n", encoding="utf-8")
    manifest = {
        "artifacts": {
            final.name: hashlib.sha256(final.read_bytes()).hexdigest(),
        },
    }
    store.update_manifest(
        run["run_id"], manifest, stage="verified", status="verified",
    )
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )

    with pytest.raises(RunVerificationError, match="data_cutoff"):
        export_topic_run(run["run_id"], "jsonl", store=store)


@pytest.mark.parametrize("text", ["=HYPERLINK(\"bad\")", "+1+1", "-1+1", "@SUM(A1)"])
def test_export_escapes_formula_like_user_text(tmp_path, text):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), CUTOFF_MS)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    row = _row(run["run_id"])
    row["source_item"]["text"] = text
    row["evidence"] = [text]
    (artifact_dir / "final_reviewed.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    store.update_status(run["run_id"], "verified", stage="verified")
    manifest = {"artifacts": {"final_reviewed.jsonl": hashlib.sha256((artifact_dir / "final_reviewed.jsonl").read_bytes()).hexdigest()}}
    store.update_manifest(run["run_id"], manifest, stage="verified", status="verified"); (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    path = export_topic_run(run["run_id"], "xlsx", store=store)
    wb = openpyxl.load_workbook(path, data_only=False)
    assert wb["反馈清单"]["C2"].value.startswith("'")


def test_export_blocks_unverified_run(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    with pytest.raises(RunVerificationError, match="run_not_verified"):
        export_topic_run(run["run_id"], "jsonl", store=store)


def test_export_rejects_tampered_final_artifact(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    final = artifact_dir / "final_reviewed.jsonl"
    final.write_text(json.dumps(_row(run["run_id"])) + "\n", encoding="utf-8")
    store.update_manifest(run["run_id"], {"artifacts": {final.name: hashlib.sha256(final.read_bytes()).hexdigest()}}, stage="verified", status="verified")
    final.write_text(json.dumps({**_row(run["run_id"]), "reason": "tampered"}) + "\n", encoding="utf-8")
    with pytest.raises(RunVerificationError, match="manifest_hash_reconciliation"):
        export_topic_run(run["run_id"], "xlsx", store=store)
