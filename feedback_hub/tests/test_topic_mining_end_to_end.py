from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute
from feedback_hub.topic_mining.api import make_router
from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.export import export_topic_run
from feedback_hub.topic_mining.run_store import TopicRunStore
from feedback_hub.topic_mining.service import (
    RunVerificationError,
    run_topic_job,
    submit_review_overrides,
    verify_topic_run,
)
from feedback_hub.topic_mining.vector_client import (
    VectorCapabilities,
    VectorHit,
    VectorSearchResult,
)


def _topic_spec(start: datetime, end: datetime):
    return validate_topic_spec({
        "schema_version": 1,
        "topic_name": "全屏时输入法工具栏不隐藏",
        "objective": "找出 Win 端游戏或视频全屏时输入法工具栏仍显示的反馈",
        "scope": {
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "platforms": ["Win"],
            "products": ["微信输入法"],
        },
        "unit": "feedback",
        "inclusion_criteria": ["游戏或视频全屏时输入法工具栏仍显示"],
        "exclusion_criteria": ["Windows 系统任务栏仍显示", "游戏黑屏"],
        "positive_examples": ["游戏全屏时输入法工具栏一直显示"],
        "negative_examples": ["进入游戏后黑屏"],
        "lexical_hints": {"objects": ["工具栏"], "contexts": ["游戏", "全屏"]},
        "classification_labels": [
            {"id": "matched", "meaning": "明确符合专题定义"},
            {"id": "not_matched", "meaning": "不符合或证据不足"},
        ],
        "output": {
            "preferred_format": "xlsx",
            "required_fields": ["feedback_text", "feedback_time", "source_url"],
        },
    })


def _create_source(path, start: datetime, end: datetime) -> None:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE feedback (feedback_id TEXT PRIMARY KEY, conversation_id TEXT, "
            "msg_seq INTEGER, ts_ms INTEGER, platform TEXT, appversion TEXT, channel TEXT, "
            "device_name TEXT, user_vid TEXT, service_vid INTEGER, external_chat_url TEXT, text TEXT)"
        )
        connection.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("win-game", "c-game", 1, start_ms, "Win", "1", "pc", "PC", "u1", 1,
             "https://example.test/game", "游戏全屏时输入法工具栏一直显示"),
            ("win-taskbar", "c-taskbar", 1, start_ms + 1, "Win", "1", "pc", "PC", "u2", 2,
             "https://example.test/taskbar", "全屏时 Windows 系统任务栏仍显示"),
            ("win-black-screen", "c-black", 1, start_ms + 2, "Win", "1", "pc", "PC", "u3", 3,
             "https://example.test/black", "游戏进入全屏后黑屏"),
            ("mac-semantic", "c-mac", 1, start_ms + 3, "Mac", "1", "pc", "Mac", "u4", 4,
             "https://example.test/mac", "视频全屏时输入法工具栏一直显示"),
            ("win-paraphrase", "c-vector", 1, start_ms + 4, "Win", "1", "pc", "PC", "u5", 5,
             "https://example.test/vector", "沉浸场景悬浮控件遮住画面且始终没有收起"),
            # This exclusive-end boundary establishes source coverage without
            # joining the scoped Win candidate set.
            ("coverage-boundary", "c-boundary", 1, end_ms, "Win", "1", "pc", "PC", "u6", 6,
             "https://example.test/boundary", "范围结束边界"),
        ])


def test_complete_fixture_run_keeps_source_read_only_and_exports_verified_win_matches(tmp_path):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    source = tmp_path / "source.db"
    _create_source(source, start, end)
    source_sha_before = hashlib.sha256(source.read_bytes()).hexdigest()

    config = TopicMiningConfig(
        source_db_path=source,
        data_dir=tmp_path / "topic-mining-data",
        vector_api_url="https://vector.example.test",
        vector_max_lag_seconds=1_000_000,
        classifier_batch_size=20,
        classifier_concurrency=1,
    )
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_topic_spec(start, end), int(end.timestamp() * 1000))

    class FakeVector:
        def capabilities(self):
            return VectorCapabilities("feedback-items-v1", 1, ("feedback",), int(end.timestamp() * 1000))

        def search(self, _queries, filters, _limit):
            assert filters["platforms"] == ["Win"]
            return VectorSearchResult(
                "feedback-items-v1",
                int(end.timestamp() * 1000),
                (
                    # The paraphrase has no lexical overlap with the topic and
                    # reaches classification exclusively through vector recall.
                    VectorHit("win-paraphrase", "objective:0", 0.99, 1),
                    # Out-of-scope identifiers are treated as recall rejects.
                    VectorHit("mac-semantic", "objective:0", 0.98, 2),
                ),
            )

    route = ModelRoute("fixture-model", "openai_compatible", "https://model.example.test", "fixture-secret", "fixture")

    def model_call(prompt, **_kwargs):
        candidate_ids = {row["item_id"] for row in json.loads(prompt)["candidates"]}
        results = {
            "win-game": {"label": "matched", "evidence": ["输入法工具栏一直显示"], "reason": "全屏工具栏仍显示"},
            # Deliberately corrected by the required review override below.
            "win-taskbar": {"label": "matched", "evidence": ["Windows 系统任务栏仍显示"], "reason": "需要人工排除"},
            "win-black-screen": {"label": "not_matched", "evidence": [], "reason": "仅描述黑屏"},
            "win-paraphrase": {"label": "not_matched", "evidence": [], "reason": "分类器证据不足"},
        }
        return ModelReply(
            json.dumps({"results": [
                {"item_id": item_id, **results[item_id], "confidence": 0.95, "needs_review": False}
                for item_id in sorted(candidate_ids)
            ]}, ensure_ascii=False),
            route.name, route.endpoint_class, route.model, 1, 1, (),
        )

    # These public aliases are the test seam promised by the distributable
    # integration contract; legacy classifier_* aliases remain supported.
    outcome = run_topic_job(
        run["run_id"], store=store, config=config, vector_client=FakeVector(),
        model_routes=[route], model_call_fn=model_call,
    )
    assert outcome["status"] == "review_ready", outcome

    expected_funnel = {
        "hard_scope_count": 4,
        "candidate_count": 4,
        "classified_count": 4,
        "review_queue_count": 4,
    }
    persisted_manifest = json.loads(store.get(run["run_id"])["manifest_json"])
    assert {
        key: persisted_manifest["funnel"][key]
        for key in expected_funnel
    } == expected_funnel

    app = FastAPI()
    app.include_router(make_router(config=config, store=store))
    with TestClient(app) as client:
        response = client.get(f"/api/topic-mining/runs/{run['run_id']}")
    assert response.status_code == 200
    public_funnel = response.json()["quality"]["funnel"]
    assert {key: public_funnel[key] for key in expected_funnel} == expected_funnel

    artifact_dir = config.data_dir / "runs" / run["run_id"]
    recalls = [json.loads(line) for line in (artifact_dir / "recall_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    classifications = [json.loads(line) for line in (artifact_dir / "classified.jsonl").read_text(encoding="utf-8").splitlines()]
    by_id = {row["item_id"]: row for row in recalls}
    assert set(by_id) == {"win-game", "win-taskbar", "win-black-screen", "win-paraphrase"}
    assert by_id["win-paraphrase"]["channels"] == ["vector"]
    assert "mac-semantic" not in by_id
    assert {row["item_id"] for row in classifications} == set(by_id)

    overrides_path = artifact_dir / "review_overrides.jsonl"
    before_invalid_submit = overrides_path.read_bytes()
    with pytest.raises(ValueError, match="evidence"):
        submit_review_overrides(run["run_id"], [{
            "item_id": "win-paraphrase", "label": "matched",
            "reason": "无根据改判", "reviewer": "fixture-reviewer",
            "evidence": ["原文中不存在的证据"],
        }], store=store)
    assert overrides_path.read_bytes() == before_invalid_submit

    submit_review_overrides(run["run_id"], [
        {
            "item_id": "win-taskbar", "label": "not_matched",
            "reason": "专题明确排除 Windows 系统任务栏", "reviewer": "fixture-reviewer",
        },
        {
            "item_id": "win-paraphrase", "label": "matched",
            "reason": "受控原文明确描述全屏遮挡", "reviewer": "fixture-reviewer",
            "evidence": ["悬浮控件遮住画面"],
        },
    ], store=store)
    persisted_overrides = [json.loads(line) for line in overrides_path.read_text(encoding="utf-8").splitlines()]
    assert persisted_overrides[1]["evidence"] == ["悬浮控件遮住画面"]

    # Even a forged manifest cannot turn ungrounded override evidence into a
    # verified claim: verification re-checks merged evidence against sources.
    valid_override_bytes = overrides_path.read_bytes()
    valid_manifest = json.loads(store.get(run["run_id"])["manifest_json"])
    tampered_overrides = json.loads(valid_override_bytes.decode("utf-8").splitlines()[0]), json.loads(valid_override_bytes.decode("utf-8").splitlines()[1])
    tampered_overrides[1]["evidence"] = ["伪造证据"]
    overrides_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in tampered_overrides), encoding="utf-8")
    tampered_manifest = json.loads(json.dumps(valid_manifest))
    override_hash = hashlib.sha256(overrides_path.read_bytes()).hexdigest()
    tampered_manifest["artifacts"]["review_overrides.jsonl"] = override_hash
    for stage_name in ("review_queue", "review_ready"):
        for direction in ("inputs", "outputs"):
            if "review_overrides.jsonl" in tampered_manifest["stages"][stage_name][direction]:
                tampered_manifest["stages"][stage_name][direction]["review_overrides.jsonl"] = override_hash
    store.update_manifest(run["run_id"], tampered_manifest, stage="review_ready")
    (artifact_dir / "manifest.json").write_text(json.dumps(tampered_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RunVerificationError, match="invalid_evidence"):
        verify_topic_run(run["run_id"], store=store)
    overrides_path.write_bytes(valid_override_bytes)
    store.update_manifest(run["run_id"], valid_manifest, stage="review_ready")
    (artifact_dir / "manifest.json").write_text(json.dumps(valid_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    assert verify_topic_run(run["run_id"], store=store) == {
        "run_id": run["run_id"], "status": "verified", "matched_count": 2,
    }
    final_reviewed = [json.loads(line) for line in (artifact_dir / "final_reviewed.jsonl").read_text(encoding="utf-8").splitlines()]
    final_by_id = {row["item_id"]: row for row in final_reviewed}
    assert final_by_id["win-paraphrase"]["evidence"] == ["悬浮控件遮住画面"]
    assert {row["data_cutoff_ms"] for row in final_reviewed} == {int(end.timestamp() * 1000)}
    workbook_path = export_topic_run(run["run_id"], "xlsx", store=store)
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    sheet = workbook["反馈清单"]
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    feedback_id_column = headers.index("Feedback ID")
    workbook_ids = {
        row[feedback_id_column]
        for row in sheet.iter_rows(min_row=2, values_only=True)
        if row[feedback_id_column]
    }
    workbook.close()
    assert workbook_ids == {"win-game", "win-paraphrase"}
    assert "数据截止时间" in headers
    final_results = [json.loads(line) for line in (artifact_dir / "final_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["data_cutoff_ms"] for row in final_results} == {int(end.timestamp() * 1000)}
    quality_report = json.loads((artifact_dir / "quality_report.json").read_text(encoding="utf-8"))
    assert quality_report["data_cutoff_ms"] == int(end.timestamp() * 1000)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha_before
