from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.service import (
    RunVerificationError,
    _validate_run_identity,
    submit_review_overrides,
    verify_topic_run,
)
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
        connection.execute(
            """CREATE TABLE feedback_source_coverage (
                channel TEXT NOT NULL, start_ts_ms INTEGER NOT NULL,
                end_ts_ms INTEGER NOT NULL, completed_at_ms INTEGER NOT NULL
            )"""
        )
        coverage_end = end if include_end else start + 1000
        connection.execute(
            "INSERT INTO feedback_source_coverage VALUES (?, ?, ?, ?)",
            ("pc", start, coverage_end, coverage_end),
        )
    return start, end


def _review_every_queue_item(run: dict, store: TopicRunStore) -> None:
    artifact_dir = Path(run["artifact_dir"])
    queue = [
        json.loads(line)
        for line in (artifact_dir / "review_queue.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
        if line.strip()
    ]
    submit_review_overrides(
        run["run_id"],
        [
            {
                "item_id": row["item_id"],
                "label": row["label"],
                "reason": "确认现有判定",
                "reviewer": "fixture-reviewer",
            }
            for row in queue
        ],
        store=store,
    )


def _pre_mode_identity(spec, source_watermark_ms: int) -> tuple[str, str, str]:
    raw = spec.to_dict()
    raw.pop("mode")
    spec_json = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
    run_id = hashlib.sha256(
        f"{spec_hash}:{source_watermark_ms}".encode("utf-8"),
    ).hexdigest()[:16]
    return run_id, spec_hash, spec_json


def _replace_persisted_identity(
    store: TopicRunStore,
    run: dict,
    *,
    run_id: str,
    spec_hash: str,
    spec_json: str,
) -> dict:
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """UPDATE topic_run
               SET run_id = ?, spec_hash = ?, spec_json = ?
               WHERE run_id = ?""",
            (run_id, spec_hash, spec_json, run["run_id"]),
        )
    persisted = store.get(run_id)
    assert persisted is not None
    return persisted


def test_identity_accepts_historic_persisted_mode_less_run(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    source_watermark_ms = 123
    run = store.create_or_get(_spec(), source_watermark_ms)
    legacy_run_id, legacy_spec_hash, legacy_spec_json = _pre_mode_identity(
        _spec(), source_watermark_ms,
    )
    persisted = _replace_persisted_identity(
        store,
        run,
        run_id=legacy_run_id,
        spec_hash=legacy_spec_hash,
        spec_json=legacy_spec_json,
    )

    reloaded = validate_topic_spec(json.loads(persisted["spec_json"]))

    assert reloaded.mode == "standard"
    _validate_run_identity(persisted, reloaded, source_watermark_ms)


def test_identity_rejects_explicit_mode_with_historic_hash(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    source_watermark_ms = 123
    spec = _spec()
    run = store.create_or_get(spec, source_watermark_ms)
    legacy_run_id, legacy_spec_hash, _ = _pre_mode_identity(
        spec, source_watermark_ms,
    )
    persisted = _replace_persisted_identity(
        store,
        run,
        run_id=legacy_run_id,
        spec_hash=legacy_spec_hash,
        spec_json=json.dumps(
            spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ),
    )

    with pytest.raises(RunVerificationError, match="run_identity_mismatch"):
        _validate_run_identity(persisted, spec, source_watermark_ms)


def test_identity_rejects_mode_less_run_with_wrong_historic_hash(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    source_watermark_ms = 123
    spec = _spec()
    run = store.create_or_get(spec, source_watermark_ms)
    legacy_run_id, _, legacy_spec_json = _pre_mode_identity(
        spec, source_watermark_ms,
    )
    persisted = _replace_persisted_identity(
        store,
        run,
        run_id=legacy_run_id,
        spec_hash="forged",
        spec_json=legacy_spec_json,
    )

    with pytest.raises(RunVerificationError, match="run_identity_mismatch"):
        _validate_run_identity(persisted, spec, source_watermark_ms)


def test_identity_rejects_new_run_with_forged_hash(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    source_watermark_ms = 123
    spec = _spec()
    run = store.create_or_get(spec, source_watermark_ms)
    persisted = _replace_persisted_identity(
        store,
        run,
        run_id=run["run_id"],
        spec_hash="forged",
        spec_json=run["spec_json"],
    )

    with pytest.raises(RunVerificationError, match="run_identity_mismatch"):
        _validate_run_identity(persisted, spec, source_watermark_ms)


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


def test_verify_rejects_a_run_whose_persisted_identity_was_tampered(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    snapshot = Path(run["artifact_dir"]) / "source_snapshot.sqlite"
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE feedback (ts_ms INTEGER NOT NULL)")
        connection.execute("INSERT INTO feedback VALUES (123)")
        connection.execute(
            """CREATE TABLE feedback_source_coverage (
                completed_at_ms INTEGER NOT NULL
            )"""
        )
        connection.execute("INSERT INTO feedback_source_coverage VALUES (123)")
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    store.update_manifest(
        run["run_id"],
        {
            "source_watermark_ms": 123,
            "source_snapshot": {
                "max_ts_ms": 123, "coverage_watermark_ms": 123,
                "sha256": digest,
            },
            "source_sha256": digest,
            "artifacts": {snapshot.name: digest},
        },
        stage="review_ready",
        status="review_ready",
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE topic_run SET spec_hash = 'forged' WHERE run_id = ?",
            (run["run_id"],),
        )

    with pytest.raises(RunVerificationError, match="run_identity_mismatch"):
        verify_topic_run(run["run_id"], store=store)


def test_verify_requires_complete_frozen_snapshot_evidence(tmp_path):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    store.update_status(run["run_id"], "review_ready", stage="review_ready")

    with pytest.raises(RunVerificationError, match="source_snapshot_required"):
        verify_topic_run(run["run_id"], store=store)


def test_effective_cutoff_rejects_the_actual_snapshot_maximum_mismatch(tmp_path):
    from feedback_hub.topic_mining.service import _effective_data_cutoff

    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    snapshot = Path(run["artifact_dir"]) / "source_snapshot.sqlite"
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE feedback (ts_ms INTEGER NOT NULL)")
        connection.execute("INSERT INTO feedback VALUES (122)")
        connection.execute(
            """CREATE TABLE feedback_source_coverage (
                completed_at_ms INTEGER NOT NULL
            )"""
        )
        connection.execute("INSERT INTO feedback_source_coverage VALUES (123)")

    with pytest.raises(RunVerificationError, match="source_watermark_mismatch"):
        _effective_data_cutoff(
            run,
            {
                "source_watermark_ms": 123,
                "source_snapshot": {
                    "max_ts_ms": 123, "coverage_watermark_ms": 123,
                },
            },
        )


def test_db_manifest_repairs_a_tampered_disk_mirror(tmp_path):
    from feedback_hub.topic_mining.service import _load_manifest, _verify_manifest

    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = tmp_path / "runs" / run["run_id"]
    (artifact_dir / "manifest.json").write_text('{"artifacts": {"final_reviewed.jsonl": "forged"}}', encoding="utf-8")
    persisted = store.get(run["run_id"])
    manifest = _load_manifest(persisted, artifact_dir)

    _verify_manifest(manifest, artifact_dir)

    assert json.loads((artifact_dir / "manifest.json").read_text(
        encoding="utf-8",
    )) == manifest


def test_stale_worker_cannot_replace_manifest_mirror_after_reclaim(tmp_path):
    from feedback_hub.topic_mining.service import _write_manifest_mirror

    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    first = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=10_000,
    )
    stale_store = store.for_worker_claim(first.claim_token)
    manifest_path = Path(run["artifact_dir"]) / "manifest.json"
    manifest_path.write_text('{"writer": "new"}\n', encoding="utf-8")
    second = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=70_000,
    )

    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        _write_manifest_mirror(
            run["run_id"], stale_store, Path(run["artifact_dir"]),
            {"writer": "stale"},
        )

    assert second.claim_token != first.claim_token
    assert manifest_path.read_text(encoding="utf-8") == '{"writer": "new"}\n'


def test_claimed_classifier_checkpoint_is_incrementally_authenticated(
    tmp_path, monkeypatch,
):
    from feedback_hub.topic_mining.service import (
        _classification_output_safe,
        _classification_resume_safe,
        _checkpoint_attempt,
        _promote_stage_files,
        _publish_classification_progress,
    )

    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 123)
    artifact_dir = Path(run["artifact_dir"])
    (artifact_dir / "recall_candidates.jsonl").write_text("{}\n", encoding="utf-8")
    (artifact_dir / "recall_manifest.json").write_text("{}\n", encoding="utf-8")
    (artifact_dir / "item_contexts.json").write_text("{}\n", encoding="utf-8")
    first = store.claim_worker(run["run_id"], lease_seconds=60)
    claimed = store.for_worker_claim(first.claim_token)
    workspace = claimed.stage_artifact_dir(artifact_dir, "classify")
    checkpoint = workspace / "classification_batches.jsonl.checkpoint.jsonl"
    checkpoint.write_text('{"index":0,"key":"classification:00000","ok":true}\n', encoding="utf-8")
    (workspace / "classification_batches.jsonl.run_state.json").write_text(
        '{"run_status":"running","completed":1,"remaining":1}\n',
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "stages": {
            "hard_scope": {
                "outputs": {
                    "item_contexts.json": hashlib.sha256(
                        (artifact_dir / "item_contexts.json").read_bytes()
                    ).hexdigest(),
                },
            },
            "hybrid_recall": {
                "outputs": {
                    name: hashlib.sha256(
                        (artifact_dir / name).read_bytes()
                    ).hexdigest()
                    for name in (
                        "recall_candidates.jsonl",
                        "recall_manifest.json",
                    )
                },
            },
        },
    }

    _publish_classification_progress(
        run["run_id"], claimed, artifact_dir, workspace, manifest,
    )

    persisted = json.loads(store.get(run["run_id"])["manifest_json"])
    assert set(persisted["stage_attempts"]["classify"]["inputs"]) == {
        "recall_candidates.jsonl", "recall_manifest.json",
        "item_contexts.json",
    }
    assert _classification_resume_safe(persisted, artifact_dir) is True
    assert checkpoint.is_file(), "publishing must not move a scheduler's open file"
    canonical = artifact_dir / checkpoint.name
    assert canonical.read_text(encoding="utf-8") == checkpoint.read_text(encoding="utf-8")

    recall_path = artifact_dir / "recall_candidates.jsonl"
    trusted_recall = recall_path.read_bytes()
    recall_path.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(RunVerificationError, match="manifest_stage_chain"):
        _publish_classification_progress(
            run["run_id"], claimed, artifact_dir, workspace, manifest,
        )
    recall_path.write_bytes(trusted_recall)

    forged_audit = artifact_dir / "classification_audit.jsonl"
    forged_audit.write_text('{"forged":true}\n', encoding="utf-8")
    _publish_classification_progress(
        run["run_id"], claimed, artifact_dir, workspace, manifest,
    )
    persisted = json.loads(store.get(run["run_id"])["manifest_json"])
    assert _classification_output_safe(
        persisted, artifact_dir, forged_audit.name,
    ) is False

    final_workspace = claimed.stage_artifact_dir(
        artifact_dir, "classify_final_test",
    )
    staged_audit = final_workspace / forged_audit.name
    staged_audit.write_text('{"trusted":true}\n', encoding="utf-8")
    final_hashes = _promote_stage_files(
        run["run_id"], claimed, artifact_dir, final_workspace,
        [staged_audit.name],
    )
    forged_audit.write_text('{"tampered_after_final":true}\n', encoding="utf-8")
    _checkpoint_attempt(
        run["run_id"], claimed, artifact_dir, manifest, "classify",
        [forged_audit], output_hashes=final_hashes,
    )
    persisted = json.loads(store.get(run["run_id"])["manifest_json"])
    assert _classification_output_safe(
        persisted, artifact_dir, forged_audit.name,
    ) is False

    original_publisher = claimed.publish_worker_files

    def publish_then_tamper(run_id, pairs):
        digests = original_publisher(run_id, pairs)
        canonical.write_text("tampered after promotion\n", encoding="utf-8")
        return digests

    monkeypatch.setattr(
        claimed, "publish_worker_files", publish_then_tamper,
    )
    _publish_classification_progress(
        run["run_id"], claimed, artifact_dir, workspace, manifest,
    )
    persisted = json.loads(store.get(run["run_id"])["manifest_json"])
    assert _classification_output_safe(
        persisted, artifact_dir, canonical.name,
    ) is False

    second = store.claim_worker(
        run["run_id"], lease_seconds=60,
        now_ms=first.lease_expires_at_ms,
    )
    checkpoint.write_text("stale overwrite\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        _publish_classification_progress(
            run["run_id"], claimed, artifact_dir, workspace, manifest,
        )

    assert second.claim_token != first.claim_token
    assert canonical.read_text(encoding="utf-8") != "stale overwrite\n"


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
    uncovered_start, _ = _write_source(uncovered, include_end=False)
    config = TopicMiningConfig(source_db_path=uncovered, data_dir=tmp_path / "data-a", vector_api_url="https://vector.test")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), uncovered_start + 1000)
    assert run_topic_job(run["run_id"], store=store, config=config)["error_code"] == "data_coverage_error"

    covered = tmp_path / "covered.db"
    _, end = _write_source(covered)
    stale_config = TopicMiningConfig(source_db_path=covered, data_dir=tmp_path / "data-b", vector_api_url="https://vector.test", vector_max_lag_seconds=1)
    stale_store = TopicRunStore(stale_config.data_dir / "runs.db", stale_config.data_dir / "runs")
    stale_run = stale_store.create_or_get(_spec(), end)
    class StaleVector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), 0)
        def search(self, *_args): return VectorSearchResult("feedback-items-v1", 0, (VectorHit("f1", "objective:0", .9, 1),))
    outcome = run_topic_job(stale_run["run_id"], store=stale_store, config=stale_config, vector_client=StaleVector())
    assert outcome["error_code"] == "vector_index_stale"

    from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute
    class FreshVector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)
        def search(self, *_args): return VectorSearchResult("feedback-items-v1", end, (VectorHit("f1", "objective:0", .9, 1),))
    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")
    def classify(prompt, **_kwargs):
        item_ids = sorted(row["item_id"] for row in json.loads(prompt)["candidates"])
        return ModelReply(json.dumps({"results": [{"item_id": item_id, "label": "matched", "confidence": .9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False} for item_id in item_ids]}), "test", "openai_compatible", "test", 1, 1, ())

    recovered = run_topic_job(
        stale_run["run_id"], store=stale_store, config=stale_config,
        vector_client=FreshVector(), classifier_routes=[route],
        classifier_call_fn=classify,
    )

    assert recovered["status"] == "review_ready"
    recovered_manifest = json.loads(recovered["manifest_json"])
    assert recovered_manifest["unresolved_vector_items"] == 0
    with pytest.raises(RunVerificationError, match="review_decisions_incomplete"):
        verify_topic_run(stale_run["run_id"], store=stale_store)
    _review_every_queue_item(recovered, stale_store)
    assert verify_topic_run(stale_run["run_id"], store=stale_store)["status"] == "verified"
    final_rows = [
        json.loads(line)
        for line in (
            Path(recovered["artifact_dir"]) / "final_reviewed.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert {row["data_cutoff_ms"] for row in final_rows} == {end}


def test_two_day_standard_service_classifies_at_most_one_hundred_sixty(tmp_path):
    from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.export import export_topic_run
    from feedback_hub.topic_mining.service import run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    source = tmp_path / "source.db"
    start, end = _write_source(source)
    with sqlite3.connect(source) as connection:
        rows = [
            (
                f"bulk-{index:04d}", f"bulk-{index:04d}", 1, start + 2_000 + index,
                "Win", "1", "pc", "PC", f"u-{index:04d}", 1,
                f"https://example.test/{index}", "游戏全屏工具栏一直显示",
            )
            for index in range(800)
        ]
        connection.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    raw_spec = _spec().to_dict()
    raw_spec["positive_examples"] = ["工具栏遮挡", "全屏工具栏", "工具栏一直显示"]
    spec = validate_topic_spec(raw_spec)
    config = TopicMiningConfig(
        source_db_path=source,
        data_dir=tmp_path / "data",
        vector_api_url="https://vector.test",
        vector_max_lag_seconds=1_000_000,
        classifier_concurrency=1,
    )
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(spec, end)

    query_ids = ("objective:0", "positive:0", "positive:1", "positive:2")
    hits = tuple(
        VectorHit(f"bulk-{index:04d}", query_ids[index // 200], .9, index % 200 + 1)
        for index in range(800)
    )

    class Vector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)
        def search(self, *_args): return VectorSearchResult("feedback-items-v1", end, hits)

    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")

    def classify(prompt, **_kwargs):
        item_ids = [row["item_id"] for row in json.loads(prompt)["candidates"]]
        return ModelReply(json.dumps({"results": [
            {"item_id": item_id, "label": "not_matched", "confidence": .9, "evidence": [], "reason": "不符合", "needs_review": False}
            for item_id in item_ids
        ]}), "test", "openai_compatible", "test", 1, 1, ())

    outcome = run_topic_job(
        run["run_id"], store=store, config=config, vector_client=Vector(),
        classifier_routes=[route], classifier_call_fn=classify,
    )

    manifest = json.loads(outcome["manifest_json"])
    assert outcome["status"] == "review_ready"
    assert manifest["recall_pool_count"] == 800
    assert manifest["classified_count"] == 160
    assert manifest["selected_candidate_count"] == 160
    assert manifest["review_budget"] == {
        "effective_days": 2, "per_day": 20, "maximum": 80,
        "sample_limit": 40, "mandatory_count": 0,
        "sampled_count": 40, "queue_count": 40,
    }
    assert manifest["funnel"]["review_queue_count"] == 40
    _review_every_queue_item(outcome, store)

    valid_manifest = json.loads(store.get(run["run_id"])["manifest_json"])
    tampered_manifest = json.loads(json.dumps(valid_manifest))
    tampered_manifest["classified_count"] = 159
    tampered_manifest["funnel"]["classified_count"] = 159
    store.update_manifest(run["run_id"], tampered_manifest, stage="review_ready")
    artifact_dir = Path(run["artifact_dir"])
    (artifact_dir / "manifest.json").write_text(
        json.dumps(tampered_manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(RunVerificationError, match="classified_count_mismatch"):
        verify_topic_run(run["run_id"], store=store)
    store.update_manifest(run["run_id"], valid_manifest, stage="review_ready")
    (artifact_dir / "manifest.json").write_text(
        json.dumps(valid_manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    assert verify_topic_run(run["run_id"], store=store)["status"] == "verified"
    assert export_topic_run(run["run_id"], "jsonl", store=store).is_file()

    recall_ids = {
        json.loads(line)["item_id"]
        for line in (artifact_dir / "recall_candidates.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
        if line.strip()
    }
    selected_rows = [
        json.loads(line)
        for line in (artifact_dir / "selected_candidates.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
        if line.strip()
    ]
    selected_ids = [row["item_id"] for row in selected_rows]
    assert len(selected_ids) == manifest["selected_candidate_count"]
    assert len(selected_ids) == len(set(selected_ids))
    assert set(selected_ids) <= recall_ids


def test_run_maps_classifier_quota_pause(tmp_path):
    from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute, QuotaExhaustedError
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    source = tmp_path / "source.db"
    _, end = _write_source(source)
    config = TopicMiningConfig(source_db_path=source, data_dir=tmp_path / "data", vector_api_url="https://vector.test", vector_max_lag_seconds=1_000_000, classifier_concurrency=1)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), end)
    class Vector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)
        def search(self, *_args): return VectorSearchResult("feedback-items-v1", end, (VectorHit("f1", "objective:0", .9, 1),))
    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")
    def exhausted(_prompt, *, route, **_kwargs): raise QuotaExhaustedError(route.name, 429, "quota")
    first_claim = store.claim_worker(run["run_id"], lease_seconds=60)
    outcome = run_topic_job(
        run["run_id"], store=store.for_worker_claim(first_claim.claim_token),
        config=config, vector_client=Vector(), classifier_routes=[route],
        classifier_call_fn=exhausted,
    )
    assert outcome["status"] == "paused_quota_exhausted"
    attempt = json.loads(store.get(run["run_id"])["manifest_json"])["stage_attempts"]["classify"]
    assert {
        "classification_batches.jsonl.checkpoint.jsonl",
        "classification_batches.jsonl.run_state.json",
        "classification_batches.jsonl.failures.jsonl",
        "classification_audit.jsonl",
    } <= set(attempt["outputs"])

    artifact_dir = Path(run["artifact_dir"])
    snapshot_before = hashlib.sha256(
        (artifact_dir / "source_snapshot.sqlite").read_bytes()
    ).hexdigest()
    # Audit history is independently durable. Even when the scheduler
    # checkpoint is missing and cannot be reused, the quota attempt must be
    # retained and the retry must advance to a new audit generation.
    (artifact_dir / "classification_batches.jsonl.checkpoint.jsonl").unlink()

    def recovered_call(prompt, **_kwargs):
        item_ids = sorted(row["item_id"] for row in json.loads(prompt)["candidates"])
        return ModelReply(json.dumps({"results": [{
            "item_id": item_id, "label": "matched", "confidence": .9,
            "evidence": ["工具栏一直显示"], "reason": "符合",
            "needs_review": False,
        } for item_id in item_ids]}), "test", "openai_compatible", "test", 1, 1, ())

    second_claim = store.claim_worker(run["run_id"], lease_seconds=60)
    resumed = run_topic_job(
        run["run_id"], store=store.for_worker_claim(second_claim.claim_token),
        config=config, vector_client=Vector(),
        classifier_routes=[route], classifier_call_fn=recovered_call,
    )

    assert resumed["status"] == "review_ready"
    audit_rows = [
        json.loads(line)
        for line in (artifact_dir / "classification_audit.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
    ]
    assert [row["audit_generation"] for row in audit_rows] == [1, 2]
    assert hashlib.sha256(
        (artifact_dir / "source_snapshot.sqlite").read_bytes()
    ).hexdigest() == snapshot_before


def test_frozen_selection_quota_recovery_reuses_authenticated_checkpoint(tmp_path):
    from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute, QuotaExhaustedError
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import _classification_resume_safe, run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    source = tmp_path / "source.db"
    _, end = _write_source(source)
    config = TopicMiningConfig(
        source_db_path=source,
        data_dir=tmp_path / "data",
        vector_api_url="https://vector.test",
        vector_max_lag_seconds=1_000_000,
        classifier_batch_size=1,
        classifier_concurrency=1,
    )
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), end)

    class Vector:
        def capabilities(self): return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)
        def search(self, *_args): return VectorSearchResult(
            "feedback-items-v1", end,
            (
                VectorHit("f1", "objective:0", .9, 1),
                VectorHit("boundary-a", "objective:0", .8, 2),
            ),
        )

    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")
    first_attempt_batches: list[tuple[str, ...]] = []

    def reply_for(item_ids):
        return ModelReply(json.dumps({"results": [
            {
                "item_id": item_id,
                "label": "matched" if item_id == "f1" else "not_matched",
                "confidence": .9,
                "evidence": ["工具栏一直显示"] if item_id == "f1" else [],
                "reason": "符合" if item_id == "f1" else "不符合",
                "needs_review": False,
            }
            for item_id in item_ids
        ]}), "test", "openai_compatible", "test", 1, 1, ())

    def quota_after_first(prompt, *, route, **_kwargs):
        item_ids = tuple(row["item_id"] for row in json.loads(prompt)["candidates"])
        first_attempt_batches.append(item_ids)
        if len(first_attempt_batches) == 1:
            return reply_for(item_ids)
        raise QuotaExhaustedError(route.name, 429, "quota")

    first_claim = store.claim_worker(run["run_id"], lease_seconds=60)
    paused = run_topic_job(
        run["run_id"], store=store.for_worker_claim(first_claim.claim_token),
        config=config, vector_client=Vector(), classifier_routes=[route],
        classifier_call_fn=quota_after_first,
    )

    assert paused["status"] == "paused_quota_exhausted"
    artifact_dir = Path(run["artifact_dir"])
    paused_manifest = json.loads(store.get(run["run_id"])["manifest_json"])
    attempt = paused_manifest["stage_attempts"]["classify"]
    assert set(attempt["inputs"]) == {
        "recall_candidates.jsonl", "recall_manifest.json",
        "selected_candidates.jsonl", "item_contexts.json",
    }
    assert _classification_resume_safe(paused_manifest, artifact_dir) is True
    audit_before = (artifact_dir / "classification_audit.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()
    completed_ids = set(first_attempt_batches[0])
    recovery_batches: list[tuple[str, ...]] = []

    def recover_remaining(prompt, **_kwargs):
        item_ids = tuple(row["item_id"] for row in json.loads(prompt)["candidates"])
        recovery_batches.append(item_ids)
        return reply_for(item_ids)

    second_claim = store.claim_worker(run["run_id"], lease_seconds=60)
    resumed = run_topic_job(
        run["run_id"], store=store.for_worker_claim(second_claim.claim_token),
        config=config, vector_client=Vector(), classifier_routes=[route],
        classifier_call_fn=recover_remaining,
    )

    assert resumed["status"] == "review_ready"
    assert completed_ids.isdisjoint({item_id for batch in recovery_batches for item_id in batch})
    audit_after = (artifact_dir / "classification_audit.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()
    assert set(audit_before) <= set(audit_after)


def test_read_jsonl_normalizes_malformed_json(tmp_path):
    from feedback_hub.topic_mining.service import _read_jsonl

    path = tmp_path / "broken.jsonl"
    path.write_text("{broken\n", encoding="utf-8")
    with pytest.raises(RunVerificationError, match="invalid_artifact"):
        _read_jsonl(path)


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_read_jsonl_preserves_unicode_separator(tmp_path, separator):
    from feedback_hub.topic_mining.service import _read_jsonl

    path = tmp_path / "feedback.jsonl"
    expected = {"item_id": "f1", "text": f"第一段{separator}第二段"}
    path.write_text(
        json.dumps(expected, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert _read_jsonl(path) == [expected]


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_read_required_jsonl_preserves_unicode_separator(tmp_path, separator):
    from feedback_hub.topic_mining.service import _read_required_jsonl

    path = tmp_path / "feedback.jsonl"
    expected = {"item_id": "f1", "text": f"第一段{separator}第二段"}
    path.write_text(
        json.dumps(expected, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "artifacts": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
    }

    assert _read_required_jsonl(path, manifest) == [expected]


def test_source_bytes_unchanged_by_complete_fake_backed_run(tmp_path):
    from feedback_hub.topic_discovery.model_routes import ModelReply, ModelRoute
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import run_topic_job
    from feedback_hub.topic_mining.vector_client import VectorCapabilities, VectorHit, VectorSearchResult

    source = tmp_path / "source.db"
    start = int(datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2023, 11, 16, tzinfo=timezone.utc).timestamp() * 1000)
    source_generation = end + 10_000
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE feedback (feedback_id TEXT PRIMARY KEY, conversation_id TEXT, msg_seq INTEGER, ts_ms INTEGER, platform TEXT, appversion TEXT, channel TEXT, device_name TEXT, user_vid TEXT, service_vid INTEGER, external_chat_url TEXT, text TEXT)")
        connection.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("boundary-a", "c0", 1, start, "Win", "1", "pc", "PC", "u0", 1, "https://example.test/0", "范围开始"),
            ("f1", "c1", 1, start + 1000, "Win", "1", "pc", "PC", "u1", 1, "https://example.test/1", "游戏全屏工具栏一直显示"),
            ("boundary-b", "c2", 1, end, "Win", "1", "pc", "PC", "u2", 1, "https://example.test/2", "范围结束"),
        ])
        connection.execute(
            """CREATE TABLE feedback_source_coverage (
                channel TEXT NOT NULL, start_ts_ms INTEGER NOT NULL,
                end_ts_ms INTEGER NOT NULL,
                completed_at_ms INTEGER NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO feedback_source_coverage VALUES (?, ?, ?, ?)",
            ("pc", start, end, source_generation),
        )
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    config = TopicMiningConfig(source_db_path=source, data_dir=tmp_path / "data", vector_api_url="https://vector.test", vector_max_lag_seconds=1_000_000, classifier_concurrency=1)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(_spec(), source_generation)

    class FakeVector:
        def capabilities(self):
            return VectorCapabilities("feedback-items-v1", 1, ("feedback",), source_generation)
        def search(self, *_args):
            return VectorSearchResult("feedback-items-v1", source_generation, (VectorHit("f1", "objective:0", .9, 1),))

    route = ModelRoute("test", "openai_compatible", "https://model.test", "not-a-secret", "test")
    def call_fn(_prompt, **_kwargs):
        return ModelReply(json.dumps({"results": [{"item_id": "f1", "label": "matched", "confidence": .9, "evidence": ["工具栏一直显示"], "reason": "符合", "needs_review": False}]}), "test", "openai_compatible", "test", 1, 1, ())
    outcome = run_topic_job(run["run_id"], store=store, config=config, vector_client=FakeVector(), classifier_routes=[route], classifier_call_fn=call_fn)
    assert outcome["status"] == "review_ready"
    # A missing hash-validated stage artifact resumes at that stage rather
    # than rewriting the read-only source snapshot or restarting the run.
    (config.data_dir / "runs" / run["run_id"] / "classified.jsonl").unlink()
    assert run_topic_job(run["run_id"], store=store, config=config, vector_client=FakeVector(), classifier_routes=[route], classifier_call_fn=call_fn)["status"] == "review_ready"
    current = store.get(run["run_id"])
    original_manifest = json.loads(current["manifest_json"])
    tampered_manifest = json.loads(json.dumps(original_manifest))
    for stage in tampered_manifest["stages"].values():
        stage["input_count"] = 999
    store.update_manifest(run["run_id"], tampered_manifest, stage="review_ready")
    manifest_path = config.data_dir / "runs" / run["run_id"] / "manifest.json"
    manifest_path.write_text(json.dumps(tampered_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RunVerificationError, match="manifest_stage_chain"):
        verify_topic_run(run["run_id"], store=store)
    store.update_manifest(run["run_id"], original_manifest, stage="review_ready")
    manifest_path.write_text(json.dumps(original_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _review_every_queue_item(store.get(run["run_id"]), store)
    assert verify_topic_run(run["run_id"], store=store)["status"] == "verified"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_caller_ai_run_stops_after_recall_without_invoking_model(tmp_path):
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import get_candidate_page, run_topic_job
    from feedback_hub.topic_mining.vector_client import (
        VectorCapabilities,
        VectorHit,
        VectorSearchResult,
    )

    source = tmp_path / "source.db"
    _start, watermark = _write_source(source)
    config = TopicMiningConfig(
        source_db_path=source,
        data_dir=tmp_path / "data",
        vector_api_url="https://vector.test",
        vector_max_lag_seconds=1_000_000,
    )
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(
        _spec(),
        watermark,
        classification_protocol_version=2,
        classification_owner="caller_ai",
    )

    class FakeVector:
        def capabilities(self):
            return VectorCapabilities("feedback-items-v1", 1, ("feedback",), watermark)

        def search(self, *_args):
            return VectorSearchResult(
                "feedback-items-v1",
                watermark,
                (
                    VectorHit("f1", "objective:0", .9, 1),
                    VectorHit("boundary-a", "objective:0", .8, 2),
                ),
            )

    def forbidden_model_call(*_args, **_kwargs):
        raise AssertionError("v2 topic run invoked a classifier")

    outcome = run_topic_job(
        run["run_id"],
        store=store,
        config=config,
        vector_client=FakeVector(),
        model_call_fn=forbidden_model_call,
    )

    assert outcome["status"] == "classification_ready"
    assert outcome["stage"] == "classification_ready"
    manifest = json.loads(outcome["manifest_json"])
    assert manifest["manifest_version"] == 3
    assert manifest["classification_protocol"] == {
        "version": 2,
        "owner": "caller_ai",
    }
    assert len(manifest["candidate_set_sha256"]) == 64
    artifact_dir = Path(outcome["artifact_dir"])
    assert not (artifact_dir / "classified.jsonl").exists()
    assert not (artifact_dir / "classification_audit.jsonl").exists()

    page = get_candidate_page(run["run_id"], 0, 20, store=store)
    assert page["candidate_set_sha256"] == manifest["candidate_set_sha256"]
    assert page["total"] == 2
    assert page["next_offset"] is None
    assert all(item["decision_state"] == "pending" for item in page["items"])
    assert page["topic"]["inclusion_criteria"] == list(_spec().inclusion_criteria)
    assert all("context_items" in item for item in page["items"])
    with pytest.raises(RunVerificationError, match="classification_incomplete"):
        verify_topic_run(run["run_id"], store=store)


@pytest.mark.parametrize(
    ("offset", "limit", "message"),
    [(-1, 20, "invalid_candidate_page"), (0, 21, "invalid_candidate_page")],
)
def test_candidate_page_rejects_invalid_bounds(tmp_path, offset, limit, message):
    from feedback_hub.topic_mining.service import get_candidate_page

    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(
        _spec(),
        1234,
        classification_protocol_version=2,
        classification_owner="caller_ai",
    )
    with pytest.raises(RunVerificationError, match=message):
        get_candidate_page(run["run_id"], offset, limit, store=store)


def test_candidate_page_rejects_v1_run(tmp_path):
    from feedback_hub.topic_mining.service import get_candidate_page

    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(_spec(), 1234)

    with pytest.raises(RunVerificationError, match="caller_ai_protocol_required"):
        get_candidate_page(run["run_id"], 0, 20, store=store)


def test_one_invalid_caller_decision_does_not_discard_499_valid_siblings(
    tmp_path,
):
    from feedback_hub.topic_mining.config import TopicMiningConfig
    from feedback_hub.topic_mining.service import (
        run_topic_job,
        submit_caller_classifications,
    )
    from feedback_hub.topic_mining.vector_client import (
        VectorCapabilities,
        VectorHit,
        VectorSearchResult,
    )

    source = tmp_path / "source.db"
    start = int(
        datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000
    )
    end = int(
        datetime(2023, 11, 16, tzinfo=timezone.utc).timestamp() * 1000
    )
    with sqlite3.connect(source) as connection:
        connection.execute(
            """CREATE TABLE feedback (
                feedback_id TEXT PRIMARY KEY, conversation_id TEXT,
                msg_seq INTEGER, ts_ms INTEGER, platform TEXT,
                appversion TEXT, channel TEXT, device_name TEXT,
                user_vid TEXT, service_vid INTEGER, external_chat_url TEXT,
                text TEXT
            )"""
        )
        rows = []
        for index in range(500):
            item_id = f"item-{index:04d}"
            ts_ms = start + ((end - start - 1) * index // 499)
            rows.append((
                item_id, f"conversation-{index}", 1, ts_ms, "Win", "1",
                "pc", "PC", f"user-{index}", 1,
                f"https://example.test/{index}", f"原文片段 {index}",
            ))
        connection.executemany(
            "INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            """CREATE TABLE feedback_source_coverage (
                channel TEXT NOT NULL, start_ts_ms INTEGER NOT NULL,
                end_ts_ms INTEGER NOT NULL, completed_at_ms INTEGER NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO feedback_source_coverage VALUES (?, ?, ?, ?)",
            ("pc", start, end, end),
        )
    raw_spec = _spec().to_dict()
    raw_spec["mode"] = "exhaustive"
    spec = validate_topic_spec(raw_spec)
    config = TopicMiningConfig(
        source_db_path=source,
        data_dir=tmp_path / "data",
        vector_api_url="https://vector.test",
        vector_max_lag_seconds=1_000_000,
    )
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(
        spec,
        end,
        classification_protocol_version=2,
        classification_owner="caller_ai",
    )

    class FakeVector:
        def capabilities(self):
            return VectorCapabilities("feedback-items-v1", 1, ("feedback",), end)

        def search(self, *_args):
            return VectorSearchResult(
                "feedback-items-v1",
                end,
                tuple(
                    VectorHit(
                        f"item-{index:04d}", "objective:0",
                        1 - index / 1000, index + 1,
                    )
                    for index in range(500)
                ),
            )

    run_topic_job(
        run["run_id"], store=store, config=config,
        vector_client=FakeVector(),
        model_call_fn=lambda *_args, **_kwargs: pytest.fail(
            "caller-ai run invoked backend model",
        ),
    )
    decisions = [
        {
            "item_id": f"item-{index:04d}",
            "label": "matched",
            "reason": "明确命中",
            "evidence": [
                "不是原文" if index == 317 else f"原文片段 {index}"
            ],
        }
        for index in range(500)
    ]

    response = submit_caller_classifications(
        run["run_id"], decisions, store=store,
    )

    assert len(response["accepted_ids"]) == 499
    assert response["rejected"] == [{
        "item_id": "item-0317",
        "code": "evidence_not_grounded",
    }]
    assert response["accepted_count"] == 499
    assert response["pending_count"] == 1
    assert store.get(run["run_id"])["status"] == "classification_in_progress"
    assert len(store.get_caller_decisions(run["run_id"])) == 499
    revisions_before = store.caller_revision_count(run["run_id"])

    fixed = submit_caller_classifications(
        run["run_id"],
        [{
            "item_id": "item-0317",
            "label": "matched",
            "reason": "修正",
            "evidence": ["原文片段 317"],
        }],
        store=store,
    )

    assert fixed["accepted_count"] == 500
    assert fixed["pending_count"] == 0
    assert store.get(run["run_id"])["status"] == "verification_ready"
    assert store.caller_revision_count(run["run_id"]) == revisions_before + 1

    verified = verify_topic_run(run["run_id"], store=store)
    assert verified == {
        "run_id": run["run_id"],
        "status": "verified",
        "matched_count": 500,
    }
    final_rows = [
        json.loads(line)
        for line in (
            Path(run["artifact_dir"]) / "final_reviewed.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(final_rows) == 500
    assert all(row["source"] == "caller_ai" for row in final_rows)
    from feedback_hub.topic_mining.export import export_topic_run

    workbook = export_topic_run(run["run_id"], "xlsx", store=store)
    assert workbook.is_file()
    report = json.loads(
        (Path(run["artifact_dir"]) / "quality_report.json").read_text(
            encoding="utf-8",
        )
    )
    assert report["classification_protocol_version"] == 2
    assert report["classification_owner"] == "caller_ai"
    assert report["classification_revision_count"] == 500
    assert report["pending_decision_count"] == 0
