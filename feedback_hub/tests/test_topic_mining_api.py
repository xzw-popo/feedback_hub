from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from feedback_hub.topic_mining.api import make_router
from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.run_store import TopicRunStore


def _spec():
    from feedback_hub.tests.test_topic_mining_export import _spec as shared_spec
    return shared_spec().to_dict()


def _client(tmp_path, *, token: str = ""):
    app = FastAPI()
    config = TopicMiningConfig(data_dir=tmp_path / "data", api_token=token)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    app.include_router(make_router(config=config, store=store))
    return TestClient(app)


def _artifact_client(tmp_path, *, status: str, artifacts: dict[str, bytes]):
    config = TopicMiningConfig(data_dir=tmp_path / "data")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    artifact_dir = Path(run["artifact_dir"])
    for name, content in artifacts.items():
        (artifact_dir / name).write_bytes(content)
    manifest = {
        "artifacts": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in artifacts.items()
        }
    }
    store.update_manifest(run["run_id"], manifest, stage=status, status=status)
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(make_router(config=config, store=store))
    return TestClient(app), run, artifact_dir


def test_create_run_is_idempotent_and_starts_background_job(tmp_path, monkeypatch):
    import feedback_hub.topic_mining.api as api
    started = []
    def start(run_id, **_kwargs):
        started.append(run_id)
        return SimpleNamespace(scheduled=True, reason="scheduled")
    monkeypatch.setattr(api, "start_run_async", start)
    client = _client(tmp_path)
    first = client.post("/api/topic-mining/runs", json=_spec()).json()
    second = client.post("/api/topic-mining/runs", json=_spec()).json()
    assert first["run_id"] == second["run_id"]
    assert first["scheduled"] is True
    assert second["scheduled"] is False
    assert started == [first["run_id"]]


def _resume_client(tmp_path, monkeypatch, *, status="pending", claim_now_ms=None):
    import feedback_hub.topic_mining.api as api

    config = TopicMiningConfig(
        data_dir=tmp_path / "data", worker_lease_seconds=60,
    )
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    if status != "pending":
        store.update_status(run["run_id"], status, stage=status)
    if claim_now_ms is not None:
        store.claim_worker(
            run["run_id"], lease_seconds=config.worker_lease_seconds,
            now_ms=claim_now_ms,
        )
    started = []

    class DeferredThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target = target
            self.kwargs = kwargs
            self.daemon = daemon

        def start(self):
            started.append(self.kwargs["run_id"])

    monkeypatch.setattr(api.threading, "Thread", DeferredThread)
    app = FastAPI()
    app.include_router(make_router(config=config, store=store))
    return TestClient(app), store, run, started


@pytest.mark.parametrize("status", ["pending", "paused_quota_exhausted", "failed"])
def test_resume_claims_recoverable_run_and_schedules_once(
    tmp_path, monkeypatch, status,
):
    client, store, run, started = _resume_client(
        tmp_path, monkeypatch, status=status,
    )

    first = client.post(f"/api/topic-mining/runs/{run['run_id']}/resume")
    second = client.post(f"/api/topic-mining/runs/{run['run_id']}/resume")

    assert first.status_code == 200
    assert first.json()["scheduled"] is True
    assert second.status_code == 409
    assert second.json()["detail"] == "worker_already_claimed"
    assert started == [run["run_id"]]
    assert store.get(run["run_id"])["status"] == "running"


def test_resume_rejects_fresh_running_and_recovers_stale_running(
    tmp_path, monkeypatch,
):
    fresh_client, _, fresh_run, fresh_started = _resume_client(
        tmp_path / "fresh", monkeypatch, claim_now_ms=int(time.time() * 1000),
    )
    fresh = fresh_client.post(
        f"/api/topic-mining/runs/{fresh_run['run_id']}/resume",
    )
    assert fresh.status_code == 409
    assert fresh.json()["detail"] == "worker_already_claimed"
    assert fresh_started == []

    stale_client, _, stale_run, stale_started = _resume_client(
        tmp_path / "stale", monkeypatch, claim_now_ms=1,
    )
    stale = stale_client.post(
        f"/api/topic-mining/runs/{stale_run['run_id']}/resume",
    )
    assert stale.status_code == 200
    assert stale.json()["scheduled"] is True
    assert stale_started == [stale_run["run_id"]]


def test_thread_start_failure_releases_claim_for_immediate_recovery(
    tmp_path, monkeypatch,
):
    import feedback_hub.topic_mining.api as api

    config = TopicMiningConfig(data_dir=tmp_path / "data", worker_lease_seconds=60)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    starts = []

    class FailingThenDeferredThread:
        def __init__(self, *, target, kwargs, daemon):
            self.kwargs = kwargs

        def start(self):
            starts.append(self.kwargs["run_id"])
            if len(starts) == 1:
                raise RuntimeError("thread unavailable")

    monkeypatch.setattr(api.threading, "Thread", FailingThenDeferredThread)

    first = api.start_run_async(run["run_id"], store=store, config=config)
    failed = store.get(run["run_id"])
    second = api.start_run_async(run["run_id"], store=store, config=config)

    assert first.scheduled is False
    assert first.reason == "worker_start_failed"
    assert failed["status"] == "failed"
    assert failed["error_code"] == "worker_start_failed"
    assert failed["error_message"] == "topic worker could not start"
    assert failed["worker_claim_token"] is None
    assert second.scheduled is True
    assert starts == [run["run_id"], run["run_id"]]


def test_create_reports_persisted_worker_start_failure(tmp_path, monkeypatch):
    import feedback_hub.topic_mining.api as api

    class FailingThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(api.threading, "Thread", FailingThread)

    response = _client(tmp_path).post("/api/topic-mining/runs", json=_spec())

    assert response.status_code == 200
    assert response.json()["scheduled"] is False
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "worker_start_failed"
    assert response.json()["error_message"] == "topic worker could not start"


def test_thread_construction_failure_is_persisted_and_recoverable(
    tmp_path, monkeypatch,
):
    import feedback_hub.topic_mining.api as api

    config = TopicMiningConfig(data_dir=tmp_path / "data", worker_lease_seconds=60)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)

    class ConstructionFailure:
        def __init__(self, **_kwargs):
            raise RuntimeError("cannot construct worker")

    monkeypatch.setattr(api.threading, "Thread", ConstructionFailure)

    result = api.start_run_async(run["run_id"], store=store, config=config)

    failed = store.get(run["run_id"])
    assert result.scheduled is False
    assert result.reason == "worker_start_failed"
    assert failed["status"] == "failed"
    assert failed["error_code"] == "worker_start_failed"
    assert failed["worker_claim_token"] is None


def test_heartbeat_start_failure_persists_failure_and_releases_claim(
    tmp_path, monkeypatch,
):
    import feedback_hub.topic_mining.api as api

    config = TopicMiningConfig(data_dir=tmp_path / "data", worker_lease_seconds=60)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    claim = store.claim_worker(
        run["run_id"], lease_seconds=config.worker_lease_seconds,
    )
    ran_job = []

    class FailingHeartbeatThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("heartbeat unavailable")

    monkeypatch.setattr(api.threading, "Thread", FailingHeartbeatThread)
    monkeypatch.setattr(api, "run_topic_job", lambda *_args, **_kwargs: ran_job.append(True))

    api._run_claimed_job(
        run_id=run["run_id"], store=store, config=config,
        claim_token=claim.claim_token,
    )

    failed = store.get(run["run_id"])
    assert ran_job == []
    assert failed["status"] == "failed"
    assert failed["error_code"] == "worker_heartbeat_start_failed"
    assert failed["error_message"] == "topic worker heartbeat could not start"
    assert failed["worker_claim_token"] is None


def test_heartbeat_renewal_failure_cancels_stale_pipeline_publication(
    tmp_path, monkeypatch,
):
    import feedback_hub.topic_mining.api as api

    config = TopicMiningConfig(data_dir=tmp_path / "data", worker_lease_seconds=1)
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    claim = store.claim_worker(
        run["run_id"], lease_seconds=config.worker_lease_seconds,
    )

    def renew_failure(*_args, **_kwargs):
        raise sqlite3.OperationalError("temporary renewal failure")

    def delayed_stale_publish(run_id, *, store, config):
        del config
        time.sleep(0.45)
        store.update_manifest(
            run_id, {"writer": "stale-worker"},
            stage="review_ready", status="review_ready",
        )

    monkeypatch.setattr(store, "renew_worker_claim", renew_failure)
    monkeypatch.setattr(api, "run_topic_job", delayed_stale_publish)

    api._run_claimed_job(
        run_id=run["run_id"], store=store, config=config,
        claim_token=claim.claim_token,
    )

    failed = store.get(run["run_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "worker_heartbeat_lost"
    assert failed["worker_claim_token"] is None
    assert failed["manifest_json"] == "{}"


@pytest.mark.parametrize("status", ["review_ready", "verified"])
def test_resume_rejects_nonrecoverable_run_with_conflict(
    tmp_path, monkeypatch, status,
):
    client, _, run, started = _resume_client(
        tmp_path, monkeypatch, status=status,
    )

    response = client.post(f"/api/topic-mining/runs/{run['run_id']}/resume")

    assert response.status_code == 409
    assert response.json()["detail"] == "run_not_recoverable"
    assert started == []


def test_api_token_is_required_when_configured(tmp_path):
    client = _client(tmp_path, token="top-secret")
    response = client.get("/api/topic-mining/capabilities")
    assert response.status_code == 401
    assert "top-secret" not in response.text


def test_artifact_name_is_allowlisted(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/topic-mining/runs/nope/artifacts/../../runs.db").status_code in {404, 422}


def test_all_routes_require_configured_token_and_missing_run_is_404(tmp_path):
    client = _client(tmp_path, token="top-secret")
    for method, path in [("get", "/api/topic-mining/capabilities"), ("post", "/api/topic-mining/runs"), ("get", "/api/topic-mining/runs/nope"), ("post", "/api/topic-mining/runs/nope/resume"), ("get", "/api/topic-mining/runs/nope/review-queue"), ("post", "/api/topic-mining/runs/nope/overrides"), ("post", "/api/topic-mining/runs/nope/verify"), ("post", "/api/topic-mining/runs/nope/export"), ("get", "/api/topic-mining/runs/nope/artifacts/safe.jsonl")]:
        response = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 401
    assert client.get("/api/topic-mining/runs/nope", headers={"Authorization": "Bearer top-secret"}).status_code == 404


@pytest.mark.parametrize(
    "status",
    ["pending", "running", "review_ready", "failed", "paused_quota_exhausted"],
)
def test_unverified_runs_cannot_download_any_artifact(tmp_path, status):
    client, run, _ = _artifact_client(
        tmp_path,
        status=status,
        artifacts={
            "source_snapshot.sqlite": b"FULL-SOURCE-DB",
            "final_results.jsonl": b'{"item_id":"final"}\n',
        },
    )

    for name in ("source_snapshot.sqlite", "final_results.jsonl"):
        response = client.get(
            f"/api/topic-mining/runs/{run['run_id']}/artifacts/{name}",
        )
        assert response.status_code == 404
        assert b"FULL-SOURCE-DB" not in response.content


def test_verified_run_downloads_only_final_deliverables(tmp_path):
    final_deliverables = {
        "final_results.jsonl": b'{"item_id":"final"}\n',
        "quality_report.json": b'{"status":"verified"}\n',
        "feedback_list.xlsx": b"fixture-xlsx",
    }
    internal_artifacts = {
        "source_snapshot.sqlite": b"FULL-SOURCE-DB",
        "scoped_items.jsonl": b'{"item_id":"scoped"}\n',
        "final_reviewed.jsonl": b'{"item_id":"reviewed"}\n',
        "safe.jsonl": b'{"item_id":"arbitrary"}\n',
    }
    client, run, _ = _artifact_client(
        tmp_path,
        status="verified",
        artifacts={**final_deliverables, **internal_artifacts},
    )

    for name, content in final_deliverables.items():
        response = client.get(
            f"/api/topic-mining/runs/{run['run_id']}/artifacts/{name}",
        )
        assert response.status_code == 200
        assert response.content == content
    for name in internal_artifacts:
        response = client.get(
            f"/api/topic-mining/runs/{run['run_id']}/artifacts/{name}",
        )
        assert response.status_code == 404
        assert b"FULL-SOURCE-DB" not in response.content


def test_verified_final_artifact_still_requires_matching_hash(tmp_path):
    client, run, artifact_dir = _artifact_client(
        tmp_path,
        status="verified",
        artifacts={"final_results.jsonl": b'{"item_id":"final"}\n'},
    )
    (artifact_dir / "final_results.jsonl").write_bytes(b"tampered\n")

    response = client.get(
        f"/api/topic-mining/runs/{run['run_id']}/artifacts/final_results.jsonl",
    )

    assert response.status_code == 409


def test_export_blocks_unverified(tmp_path):
    config = TopicMiningConfig(data_dir=tmp_path / "data")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    app = FastAPI()
    app.include_router(make_router(config=config, store=store))
    client = TestClient(app)
    assert client.post(
        f"/api/topic-mining/runs/{run['run_id']}/export",
        json={"format": "xlsx"},
    ).status_code == 409


def test_main_api_import_does_not_require_openpyxl():
    repo_root = Path(__file__).resolve().parents[2]
    script = """
import importlib.abc
import sys

class BlockOpenpyxl(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "openpyxl" or fullname.startswith("openpyxl."):
            raise ModuleNotFoundError("openpyxl blocked by regression test")
        return None

sys.meta_path.insert(0, BlockOpenpyxl())
try:
    import openpyxl
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("test import hook did not block openpyxl")

import feedback_hub.api
print("IMPORT_OK")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "IMPORT_OK"


def test_jsonl_export_does_not_require_openpyxl():
    repo_root = Path(__file__).resolve().parents[2]
    script = r'''
import hashlib
import importlib.abc
import json
import sys
import tempfile
from pathlib import Path

class BlockOpenpyxl(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "openpyxl" or fullname.startswith("openpyxl."):
            raise ModuleNotFoundError("openpyxl blocked by regression test")
        return None

sys.meta_path.insert(0, BlockOpenpyxl())

from fastapi import FastAPI
from fastapi.testclient import TestClient
from feedback_hub.topic_mining.api import make_router
from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.run_store import TopicRunStore

spec = validate_topic_spec({
    "schema_version": 1,
    "topic_name": "topic",
    "objective": "find issue",
    "scope": {
        "start_time": "2023-11-14T00:00:00+00:00",
        "end_time": "2023-11-16T00:00:00+00:00",
        "platforms": ["Win"],
        "products": ["微信输入法"],
    },
    "unit": "feedback",
    "inclusion_criteria": ["issue"],
    "exclusion_criteria": ["other issue"],
    "positive_examples": [],
    "negative_examples": [],
    "lexical_hints": {},
    "classification_labels": [
        {"id": "matched", "meaning": "match"},
        {"id": "not_matched", "meaning": "reject"},
    ],
    "output": {"preferred_format": "jsonl", "required_fields": ["feedback_text"]},
})
row = {
    "item_id": "f-1",
    "label": "matched",
    "confidence": 0.9,
    "evidence": ["issue evidence"],
    "reason": "matches topic",
    "source": "classifier",
    "source_item": {
        "item_id": "f-1",
        "feedback_id": "f-1",
        "conversation_id": "c-1",
        "ts_ms": 1700000000000,
        "platform": "Win",
        "text": "issue evidence",
        "source_url": "https://example.test/chat/1",
    },
}

with tempfile.TemporaryDirectory() as temporary:
    data_dir = Path(temporary)
    config = TopicMiningConfig(data_dir=data_dir)
    store = TopicRunStore(data_dir / "runs.db", data_dir / "runs")
    run = store.create_or_get(spec, 123)
    row["run_id"] = run["run_id"]
    row["data_cutoff_ms"] = run["source_watermark_ms"]
    artifact_dir = Path(run["artifact_dir"])
    final = artifact_dir / "final_reviewed.jsonl"
    final.write_text(json.dumps(row) + "\n", encoding="utf-8")
    manifest = {"artifacts": {final.name: hashlib.sha256(final.read_bytes()).hexdigest()}}
    store.update_manifest(run["run_id"], manifest, stage="verified", status="verified")
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app = FastAPI()
    app.include_router(make_router(config=config, store=store))
    response = TestClient(app).post(
        f"/api/topic-mining/runs/{run['run_id']}/export",
        json={"format": "jsonl"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["artifact_name"] == "final_results.jsonl"

print("JSONL_EXPORT_OK")
'''

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "JSONL_EXPORT_OK"


def test_invalid_spec_and_export_format_are_422(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/topic-mining/runs", json={}).status_code == 422
    config = TopicMiningConfig(data_dir=tmp_path / "data")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    app = FastAPI(); app.include_router(make_router(config=config, store=store))
    assert TestClient(app).post(f"/api/topic-mining/runs/{run['run_id']}/export", json={"format": "csv"}).status_code == 422


def test_review_queue_malformed_or_tampered_is_conflict(tmp_path):
    config = TopicMiningConfig(data_dir=tmp_path / "data")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    path = config.data_dir / "runs" / run["run_id"] / "review_queue.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    store.update_manifest(run["run_id"], {"artifacts": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}}, stage="review_ready", status="review_ready")
    app = FastAPI(); app.include_router(make_router(config=config, store=store))
    assert TestClient(app).get(f"/api/topic-mining/runs/{run['run_id']}/review-queue").status_code == 409
