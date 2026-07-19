from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import hashlib

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


def test_create_run_is_idempotent_and_starts_background_job(tmp_path, monkeypatch):
    import feedback_hub.topic_mining.api as api
    started = []
    monkeypatch.setattr(api, "start_run_async", started.append)
    client = _client(tmp_path)
    first = client.post("/api/topic-mining/runs", json=_spec()).json()
    second = client.post("/api/topic-mining/runs", json=_spec()).json()
    assert first["run_id"] == second["run_id"]
    assert started == [first["run_id"]]


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
    for method, path in [("get", "/api/topic-mining/capabilities"), ("post", "/api/topic-mining/runs"), ("get", "/api/topic-mining/runs/nope"), ("get", "/api/topic-mining/runs/nope/review-queue"), ("post", "/api/topic-mining/runs/nope/overrides"), ("post", "/api/topic-mining/runs/nope/verify"), ("post", "/api/topic-mining/runs/nope/export"), ("get", "/api/topic-mining/runs/nope/artifacts/safe.jsonl")]:
        response = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 401
    assert client.get("/api/topic-mining/runs/nope", headers={"Authorization": "Bearer top-secret"}).status_code == 404


def test_manifest_allowlists_artifacts_and_export_blocks_unverified(tmp_path):
    config = TopicMiningConfig(data_dir=tmp_path / "data")
    store = TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")
    run = store.create_or_get(validate_topic_spec(_spec()), 123)
    artifact = tmp_path / "data" / "runs" / run["run_id"] / "safe.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    store.update_manifest(run["run_id"], {"artifacts": {"safe.jsonl": hashlib.sha256(artifact.read_bytes()).hexdigest()}}, stage="review_ready")
    app = FastAPI()
    app.include_router(make_router(config=config, store=store))
    client = TestClient(app)
    assert client.get(f"/api/topic-mining/runs/{run['run_id']}/artifacts/safe.jsonl").status_code == 200
    assert client.get(f"/api/topic-mining/runs/{run['run_id']}/artifacts/other.jsonl").status_code == 404
    assert client.post(f"/api/topic-mining/runs/{run['run_id']}/export", json={"format": "xlsx"}).status_code == 409
