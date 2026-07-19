from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from feedback_hub.topic_mining.service import RunVerificationError, verify_topic_run
from feedback_hub.topic_mining.run_store import TopicRunStore


def _spec():
    from feedback_hub.tests.test_topic_mining_export import _spec as shared_spec
    return shared_spec()


def _write_source(path, *, include_end=True):
    start = int(datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2023, 11, 16, tzinfo=timezone.utc).timestamp() * 1000)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE feedback (feedback_id TEXT PRIMARY KEY, conversation_id TEXT, msg_seq INTEGER, ts_ms INTEGER, platform TEXT, appversion TEXT, channel TEXT, device_name TEXT, user_vid TEXT, service_vid INTEGER, external_chat_url TEXT, text TEXT)")
        rows = [("boundary-a", "c0", 1, start, "Win", "1", "pc", "PC", "u0", 1, "https://example.test/0", "范围开始"), ("f1", "c1", 1, start + 1000, "Win", "1", "pc", "PC", "u1", 1, "https://example.test/1", "游戏全屏工具栏一直显示")]
        if include_end:
            rows.append(("boundary-b", "c2", 1, end, "Win", "1", "pc", "PC", "u2", 1, "https://example.test/2", "范围结束"))
        connection.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return start, end


def test_verify_blocks_unresolved_classifier_failure(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    manifest = {"unresolved_classifier_items": 1, "artifacts": {}}
    store.update_manifest(run["run_id"], manifest, stage="classify")
    with pytest.raises(RunVerificationError, match="run_not_review_ready"):
        verify_topic_run(run["run_id"], store=store)


def test_verify_gate_rejects_pending_even_with_empty_artifacts(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    with pytest.raises(RunVerificationError, match="run_not_review_ready"):
        verify_topic_run(run["run_id"], store=store)


def test_db_manifest_remains_trust_anchor_when_disk_mirror_is_tampered(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    (artifact_dir / "manifest.json").write_text('{"artifacts": {"final_reviewed.jsonl": "forged"}}', encoding="utf-8")
    store.update_status(run["run_id"], "review_ready", stage="review_ready")
    with pytest.raises(RunVerificationError, match="manifest_hash_reconciliation"):
        verify_topic_run(run["run_id"], store=store)


def test_verify_rejects_duplicate_and_missing_link(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    item = {"item_id": "a", "text": "原文", "source_url": "https://example.test/a"}
    recall = {"item_id": "a", "item": item, "channels": ["bm25"], "fused_score": 1, "fused_rank": 1, "channel_ranks": {}, "raw_scores": {}, "query_ids": [], "negative_query_hits": []}
    result = {"item_id": "a", "label": "matched", "confidence": .9, "evidence": ["原文"], "reason": "x", "needs_review": False}
    (artifact_dir / "recall_candidates.jsonl").write_text(json.dumps(recall) + "\n", encoding="utf-8")
    (artifact_dir / "classified.jsonl").write_text("\n".join(json.dumps(result) for _ in range(2)) + "\n", encoding="utf-8")
    (artifact_dir / "review_overrides.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(RunVerificationError, match="run_not_review_ready"):
        verify_topic_run(run["run_id"], store=store)


def test_verify_reconciles_manifest_hashes(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    store.update_manifest(run["run_id"], {"artifacts": {"classified.jsonl": "not-a-real-hash"}}, stage="classify")
    with pytest.raises(RunVerificationError, match="run_not_review_ready"):
        verify_topic_run(run["run_id"], store=store)


def test_run_maps_data_coverage_and_stale_vector_to_stable_status(tmp_path):
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    uncovered = tmp_path / "uncovered.db"
    _write_source(uncovered, include_end=False)
    config = TopicMiningConfig(source_db_path=uncovered, data_dir=tmp_path / "data-a", vector_api_url="https://vector.test")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), 123)
    assert run_topic_job(run["run_id"], store=store, config=config)["error_code"] == "data_coverage_error"

    covered = tmp_path / "covered.db"
    _, end = _write_source(covered)
    stale_config = TopicMiningConfig(source_db_path=covered, data_dir=tmp_path / "data-b", vector_api_url="https://vector.test", vector_max_lag_seconds=1)
    stale_store = TopicRunStore(stale_config.data_dir / "runs.db", stale_config.data_dir / "runs")
    stale_run = stale_store.create_or_get(_spec(), 123)
    class StaleVector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), 0)
        def search(self, *_args): return VectorSearchResult("feedback-items-v1", 0, (VectorHit("f1", "objective:0", .9, 1),))
    outcome = run_topic_job(stale_run["run_id"], store=stale_store, config=stale_config, vector_client=StaleVector())
    assert outcome["error_code"] == "vector_index_stale"


def test_run_maps_classifier_quota_pause(tmp_path):
    from feedback_hub.topic_discovery.model_routes import ModelRoute, QuotaExhaustedError
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    source = tmp_path / "source.db"
    _, end = _write_source(source)
    config = TopicMiningConfig(source_db_path=source, data_dir=tmp_path / "data", vector_api_url="https://vector.test", vector_max_lag_seconds=1_000_000, classifier_concurrency=1)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), 123)
    class Vector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)
        def search(self, *_args): return VectorSearchResult("feedback-items-v1", end, (VectorHit("f1", "objective:0", .9, 1),))
    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")
    def exhausted(_prompt, *, route, **_kwargs): raise QuotaExhaustedError(route.name, 429, "quota")
    outcome = run_topic_job(run["run_id"], store=store, config=config, vector_client=Vector(), classifier_routes=[route], classifier_call_fn=exhausted)
    assert outcome["status"] == "paused_quota_exhausted"


def test_source_bytes_unchanged_by_complete_fake_backed_run(tmp_path):
    from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    source = tmp_path / "source.db"
    start = int(datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2023, 11, 16, tzinfo=timezone.utc).timestamp() * 1000)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE feedback (feedback_id TEXT PRIMARY KEY, conversation_id TEXT, msg_seq INTEGER, ts_ms INTEGER, platform TEXT, appversion TEXT, channel TEXT, device_name TEXT, user_vid TEXT, service_vid INTEGER, external_chat_url TEXT, text TEXT)")
        connection.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("boundary-a", "c0", 1, start, "Win", "1", "pc", "PC", "u0", 1, "https://example.test/0", "范围开始"),
            ("f1", "c1", 1, start + 1000, "Win", "1", "pc", "PC", "u1", 1, "https://example.test/1", "游戏全屏工具栏一直显示"),
            ("boundary-b", "c2", 1, end, "Win", "1", "pc", "PC", "u2", 1, "https://example.test/2", "范围结束"),
        ])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    config = TopicMiningConfig(source_db_path=source, data_dir=tmp_path / "data", vector_api_url="https://vector.test", vector_max_lag_seconds=1_000_000, classifier_concurrency=1)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), 123)

    class FakeVector:
        def capabilities(self):
            return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)
        def search(self, *_args):
            return VectorSearchResult("feedback-items-v1", end, (VectorHit("f1", "objective:0", .9, 1),))

    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")
    def call_fn(_prompt, **_kwargs):
        return ModelReply(json.dumps({"results": [{"item_id": "f1", "label": "matched", "confidence": .9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}]}), "test", "openai_compatible", "test", 1, 1, ())
    outcome = run_topic_job(run["run_id"], store=store, config=config, vector_client=FakeVector(), classifier_routes=[route], classifier_call_fn=call_fn)
    assert outcome["status"] == "review_ready"
    # A missing hash-validated stage artifact resumes at that stage rather
    # than rewriting the read-only source snapshot or restarting the run.
    (config.data_dir / "runs" / run["run_id"] / "classified.jsonl").unlink()
    assert run_topic_job(run["run_id"], store=store, config=config, vector_client=FakeVector(), classifier_routes=[route], classifier_call_fn=call_fn)["status"] == "review_ready"
    assert verify_topic_run(run["run_id"], store=store)["status"] == "verified"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
