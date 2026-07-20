import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.run_store import TopicRunStore


@pytest.fixture
def valid_topic_spec():
    return validate_topic_spec(
        {
            "schema_version": 1,
            "topic_name": "工具栏遮挡",
            "objective": "找出全屏时工具栏遮挡的反馈",
            "scope": {
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-02T00:00:00+00:00",
                "platforms": ["Win"],
            },
            "unit": "feedback",
            "inclusion_criteria": ["工具栏遮挡"],
            "exclusion_criteria": ["无关"],
            "positive_examples": [],
            "negative_examples": [],
            "lexical_hints": {"objects": ["工具栏"], "contexts": ["全屏"]},
            "classification_labels": [
                {"id": "matched", "meaning": "符合"},
                {"id": "not_matched", "meaning": "不符合"},
            ],
            "output": {"preferred_format": "jsonl", "required_fields": ["feedback_text"]},
        }
    )


def test_create_run_is_idempotent_for_spec_and_source_watermark(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")

    first = store.create_or_get(valid_topic_spec, source_watermark_ms=1234)
    second = store.create_or_get(valid_topic_spec, source_watermark_ms=1234)

    assert first["run_id"] == second["run_id"]
    assert first["status"] == "pending"
    assert first["created"] is True
    assert second["created"] is False
    assert first["run_id"] == hashlib.sha256(
        f"{first['spec_hash']}:1234".encode()
    ).hexdigest()[:16]


def test_same_spec_with_new_source_watermark_creates_new_run(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")

    assert store.create_or_get(valid_topic_spec, 1234)["run_id"] != store.create_or_get(
        valid_topic_spec, 5678
    )["run_id"]


def test_create_run_uses_independent_database_and_artifact_dir(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")

    run = store.create_or_get(valid_topic_spec, 1234)

    assert (tmp_path / "runs.db").is_file()
    assert run["artifact_dir"] == str(tmp_path / "runs" / run["run_id"])
    assert (tmp_path / "runs" / run["run_id"]).is_dir()


def test_mkdir_failure_does_not_publish_a_run(tmp_path, valid_topic_spec, monkeypatch):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    original_mkdir = type(tmp_path).mkdir

    def fail_run_directory(path, *args, **kwargs):
        if path.parent == tmp_path / "runs":
            raise OSError("artifact storage unavailable")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "mkdir", fail_run_directory)

    with pytest.raises(OSError, match="artifact storage unavailable"):
        store.create_or_get(valid_topic_spec, 1234)

    connection = sqlite3.connect(tmp_path / "runs.db")
    try:
        assert connection.execute("SELECT COUNT(*) FROM topic_run").fetchone()[0] == 0
    finally:
        connection.close()


def test_existing_run_repairs_missing_artifact_directory(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    first = store.create_or_get(valid_topic_spec, 1234)
    artifact_dir = tmp_path / "runs" / first["run_id"]
    artifact_dir.rmdir()

    repaired = store.create_or_get(valid_topic_spec, 1234)

    assert repaired["created"] is False
    assert artifact_dir.is_dir()


def test_existing_run_database_is_migrated_with_worker_lease_columns(tmp_path):
    db_path = tmp_path / "runs.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE topic_run (
                run_id TEXT PRIMARY KEY,
                spec_hash TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                source_watermark_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                artifact_dir TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                manifest_json TEXT NOT NULL,
                UNIQUE(spec_hash, source_watermark_ms)
            )"""
        )
        connection.execute(
            "INSERT INTO topic_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-running", "legacy-spec", "{}", 123, "running", "classify",
                None, None, str(tmp_path / "runs" / "legacy-running"), 1, 1, "{}",
            ),
        )

    store = TopicRunStore(db_path, tmp_path / "runs")

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(topic_run)")
        }
    assert {
        "worker_claim_token", "worker_claimed_at_ms", "worker_lease_expires_at_ms",
        "publication_json",
    } <= columns
    # A legacy running row has no live lease and is therefore an orphan that
    # can be reclaimed after the schema upgrade.
    claim = store.claim_worker("legacy-running", lease_seconds=60, now_ms=10_000)
    assert claim.claimed is True


def test_pending_worker_claim_is_atomic_under_concurrency(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)

    def claim_once(_index):
        return store.claim_worker(run["run_id"], lease_seconds=60, now_ms=10_000)

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(claim_once, range(8)))

    assert sum(claim.claimed for claim in claims) == 1
    assert {claim.reason for claim in claims if not claim.claimed} == {
        "worker_already_claimed",
    }


@pytest.mark.parametrize("status", ["paused_quota_exhausted", "failed"])
def test_explicit_claim_recovers_paused_or_failed_run(
    tmp_path, valid_topic_spec, status,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    store.update_status(run["run_id"], status, stage="classify")

    claim = store.claim_worker(run["run_id"], lease_seconds=60, now_ms=10_000)

    assert claim.claimed is True
    assert claim.claim_token
    current = store.get(run["run_id"])
    assert current["status"] == "running"
    assert current["error_code"] is None
    assert current["error_message"] is None


def test_running_claim_is_rejected_until_backend_lease_is_stale(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    first = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=10_000,
    )

    fresh = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=69_999,
    )
    stale = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=70_000,
    )

    assert first.claimed is True
    assert fresh.claimed is False
    assert fresh.reason == "worker_already_claimed"
    assert stale.claimed is True
    assert stale.claim_token != first.claim_token


def test_only_current_worker_token_can_renew_the_backend_lease(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    claim = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=10_000,
    )

    assert store.renew_worker_claim(
        run["run_id"], "wrong-token", lease_seconds=60, now_ms=60_000,
    ) is False
    assert store.renew_worker_claim(
        run["run_id"], claim.claim_token, lease_seconds=60, now_ms=60_000,
    ) is True
    assert store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=70_000,
    ).reason == "worker_already_claimed"
    assert store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=120_000,
    ).claimed is True


def test_expired_worker_cannot_renew_or_publish(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    claim = store.claim_worker(
        run["run_id"], lease_seconds=1, now_ms=1_000,
    )

    assert store.renew_worker_claim(
        run["run_id"], claim.claim_token,
        lease_seconds=1, now_ms=2_000,
    ) is False
    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        store.assert_worker_claim(
            run["run_id"], claim.claim_token, now_ms=2_000,
        )


def test_stale_generation_cannot_promote_over_new_worker_artifact(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    first = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=10_000,
    )
    canonical = Path(run["artifact_dir"]) / "classified.jsonl"
    canonical.write_text("new-worker-sentinel\n", encoding="utf-8")
    staged = tmp_path / "stale-classified.jsonl"
    staged.write_text("stale-worker-output\n", encoding="utf-8")
    second = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=70_000,
    )

    assert hasattr(store, "publish_worker_files")
    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        store.publish_worker_files(
            run["run_id"], first.claim_token, [(staged, canonical)],
            now_ms=70_001,
        )

    assert first.claim_token != second.claim_token
    assert canonical.read_text(encoding="utf-8") == "new-worker-sentinel\n"
    assert store.get(run["run_id"])["worker_claim_token"] == second.claim_token


def test_active_worker_publishes_snapshot_without_moving_open_staging_file(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    claim = store.claim_worker(run["run_id"], lease_seconds=60)
    claimed = store.for_worker_claim(claim.claim_token)
    artifact_dir = Path(run["artifact_dir"])
    workspace = claimed.stage_artifact_dir(artifact_dir, "classify")
    source = workspace / "classification_partial_audit" / "g1_batch.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"generation": 1}\n', encoding="utf-8")
    target = artifact_dir / "classification_partial_audit" / source.name

    published = claimed.publish_worker_files(
        run["run_id"], [(source, target)],
    )

    assert source.read_text(encoding="utf-8") == '{"generation": 1}\n'
    assert target.read_text(encoding="utf-8") == '{"generation": 1}\n'
    assert published[target.resolve()] == hashlib.sha256(
        source.read_bytes(),
    ).hexdigest()


def test_worker_cannot_promote_snapshot_when_lease_expires_during_copy(
    tmp_path, valid_topic_spec, monkeypatch,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    claim = store.claim_worker(
        run["run_id"], lease_seconds=1, now_ms=1_000,
    )
    claimed = store.for_worker_claim(claim.claim_token)
    artifact_dir = Path(run["artifact_dir"])
    workspace = claimed.stage_artifact_dir(artifact_dir, "classify")
    source = workspace / "classified.jsonl"
    source.write_text("staged\n", encoding="utf-8")
    target = artifact_dir / source.name
    clock = iter((1.0, 3.0))
    monkeypatch.setattr(
        "feedback_hub.topic_mining.run_store.time.time",
        lambda: next(clock),
    )

    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        store.publish_worker_files(
            run["run_id"], claim.claim_token, [(source, target)],
        )

    assert not target.exists()


def test_worker_rechecks_lease_after_waiting_for_publication_lock(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    claim = store.claim_worker(run["run_id"], lease_seconds=1)
    claimed = store.for_worker_claim(claim.claim_token)
    artifact_dir = Path(run["artifact_dir"])
    workspace = claimed.stage_artifact_dir(artifact_dir, "classify")
    source = workspace / "classified.jsonl"
    source.write_text("staged\n", encoding="utf-8")
    target = artifact_dir / source.name

    blocker = sqlite3.connect(store.db_path, timeout=30)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                store.publish_worker_files,
                run["run_id"], claim.claim_token, [(source, target)],
            )
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline and not list(
                artifact_dir.glob(f".{target.name}.*.tmp")
            ):
                time.sleep(0.01)
            prepared = bool(list(artifact_dir.glob(f".{target.name}.*.tmp")))
            remaining = claim.lease_expires_at_ms / 1000 - time.time()
            if remaining > 0:
                time.sleep(remaining + 0.05)
            blocker.commit()
            assert prepared
            with pytest.raises(RuntimeError, match="worker_claim_lost"):
                future.result(timeout=5)
    finally:
        blocker.close()

    assert not target.exists()


def test_manifest_update_rechecks_lease_after_waiting_for_database_lock(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    claim = store.claim_worker(run["run_id"], lease_seconds=1)
    started = Event()

    def update_manifest() -> None:
        started.set()
        store.update_manifest(
            run["run_id"], {"writer": "expired"}, stage="classify",
            worker_claim_token=claim.claim_token,
        )

    blocker = sqlite3.connect(store.db_path, timeout=30)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(update_manifest)
            assert started.wait(timeout=1)
            remaining = claim.lease_expires_at_ms / 1000 - time.time()
            if remaining > 0:
                time.sleep(remaining + 0.05)
            blocker.commit()
            with pytest.raises(RuntimeError, match="worker_claim_lost"):
                future.result(timeout=5)
    finally:
        blocker.close()

    assert store.get(run["run_id"])["manifest_json"] == "{}"


def test_unfenced_stale_worker_cannot_publish_over_new_claim(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    first = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=10_000,
    )
    stale_worker_store = store.for_worker_claim(first.claim_token)
    second = store.claim_worker(
        run["run_id"], lease_seconds=60, now_ms=70_000,
    )

    # This is the old service call shape: without a matching worker token it
    # must not publish or clear the newer owner.
    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        store.update_manifest(
            run["run_id"], {"writer": "stale-first"},
            stage="review_ready", status="review_ready",
        )
    with pytest.raises(RuntimeError, match="worker_claim_lost"):
        stale_worker_store.update_manifest(
            run["run_id"], {"writer": "tokened-stale-first"},
            stage="review_ready", status="review_ready",
        )

    current = store.get(run["run_id"])
    assert first.claim_token != second.claim_token
    assert current["status"] == "running"
    assert current["worker_claim_token"] == second.claim_token
    assert current["manifest_json"] == "{}"


@pytest.mark.parametrize("status", ["review_ready", "verified"])
def test_terminal_run_cannot_be_claimed(tmp_path, valid_topic_spec, status):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    store.update_status(run["run_id"], status, stage=status)

    claim = store.claim_worker(run["run_id"], lease_seconds=60, now_ms=10_000)

    assert claim.claimed is False
    assert claim.reason == "run_not_recoverable"


def test_terminal_publication_recovers_after_files_precede_database_commit(
    tmp_path, valid_topic_spec, monkeypatch,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    store.update_manifest(
        run["run_id"], {"artifacts": {}},
        stage="review_ready", status="review_ready",
    )
    before = store.get(run["run_id"])

    def crash_before_database_commit(*_args, **_kwargs):
        raise OSError("simulated process crash")

    monkeypatch.setattr(
        store, "_finalize_terminal_publication",
        crash_before_database_commit,
    )
    with pytest.raises(OSError, match="simulated process crash"):
        store.publish_terminal_artifacts(
            run["run_id"],
            expected_manifest_json=before["manifest_json"],
            manifest={"artifacts": {}, "verified": {"matched_count": 1}},
            stage="verified",
            status="verified",
            files={"final_reviewed.jsonl": b'{"item_id":"f1"}\n'},
        )

    with sqlite3.connect(store.db_path) as connection:
        persisted = connection.execute(
            "SELECT status, manifest_json, publication_json FROM topic_run WHERE run_id = ?",
            (run["run_id"],),
        ).fetchone()
    assert persisted[0] == "review_ready"
    assert persisted[1] == before["manifest_json"]
    assert persisted[2]
    with pytest.raises(RuntimeError, match="run_publication_conflict"):
        store.update_manifest(
            run["run_id"], {"writer": "concurrent"}, stage="review_ready",
        )

    recovered_store = TopicRunStore(store.db_path, tmp_path / "runs")
    assert recovered_store.recover_terminal_publication(run["run_id"]) is True
    recovered = recovered_store.get(run["run_id"])
    recovered_manifest = json.loads(recovered["manifest_json"])
    assert recovered["status"] == "verified"
    assert recovered["stage"] == "verified"
    assert recovered["publication_json"] is None
    assert recovered_manifest["artifacts"]["final_reviewed.jsonl"] == hashlib.sha256(
        b'{"item_id":"f1"}\n',
    ).hexdigest()
    assert (Path(run["artifact_dir"]) / "final_reviewed.jsonl").read_bytes() == b'{"item_id":"f1"}\n'


def test_terminal_publication_rejects_stale_manifest_without_lost_update(
    tmp_path, valid_topic_spec,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    store.update_manifest(
        run["run_id"], {"artifacts": {}},
        stage="verified", status="verified",
    )
    before = store.get(run["run_id"])
    store.publish_terminal_artifacts(
        run["run_id"],
        expected_manifest_json=before["manifest_json"],
        manifest={"artifacts": {}},
        stage="verified",
        status="verified",
        files={"feedback_list.xlsx": b"first-export"},
    )

    with pytest.raises(RuntimeError, match="run_publication_conflict"):
        store.publish_terminal_artifacts(
            run["run_id"],
            expected_manifest_json=before["manifest_json"],
            manifest={"artifacts": {}},
            stage="verified",
            status="verified",
            files={"final_results.jsonl": b"stale-export\n"},
        )

    current = store.get(run["run_id"])
    manifest = json.loads(current["manifest_json"])
    assert set(manifest["artifacts"]) == {"feedback_list.xlsx"}
    assert (Path(run["artifact_dir"]) / "feedback_list.xlsx").read_bytes() == b"first-export"
    assert not (Path(run["artifact_dir"]) / "final_results.jsonl").exists()


def test_committed_terminal_publication_tolerates_mirror_write_failure(
    tmp_path, valid_topic_spec, monkeypatch,
):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    run = store.create_or_get(valid_topic_spec, 1234)
    store.update_manifest(
        run["run_id"], {"artifacts": {}},
        stage="verified", status="verified",
    )
    before = store.get(run["run_id"])

    def mirror_unavailable(*_args, **_kwargs):
        raise OSError("mirror unavailable")

    monkeypatch.setattr(store, "_write_manifest_mirror", mirror_unavailable)

    store.publish_terminal_artifacts(
        run["run_id"],
        expected_manifest_json=before["manifest_json"],
        manifest={"artifacts": {}},
        stage="verified",
        status="verified",
        files={"final_results.jsonl": b"published\n"},
    )

    current = store.get(run["run_id"])
    manifest = json.loads(current["manifest_json"])
    assert current["publication_json"] is None
    assert manifest["artifacts"]["final_results.jsonl"] == hashlib.sha256(
        b"published\n",
    ).hexdigest()
