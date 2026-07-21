"""Operational JSON contract for the feedback vector CLI."""
from __future__ import annotations

import json
import fcntl
from dataclasses import replace
from types import SimpleNamespace

import pytest

from feedback_hub import db
from feedback_hub.cli import main
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.repository import VectorRepository
from feedback_hub.vector_index.shards import ShardStore
from feedback_hub.vector_index.sync import process_lock


@pytest.fixture
def vector_fixture(tmp_path):
    config = replace(
        VectorIndexConfig(),
        db_path=tmp_path / "feedback.db",
        data_dir=tmp_path / "vectors",
        model_dir=tmp_path / "model",
    )
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    repository = VectorRepository(connection)
    repository.init_schema()
    store = ShardStore.from_config(config)
    manifest = store.publish_manifest([], watermark_ts_ms=0)
    repository.mark_publication(
        publication_key=str(store.root), model_version=config.model_version,
        generation=manifest.generation, watermark_ts_ms=manifest.watermark_ts_ms,
    )
    connection.commit()
    connection.close()
    return config


def _base_args(config: VectorIndexConfig) -> list[str]:
    return ["--db", str(config.db_path), "--data-dir", str(config.data_dir), "--model-dir", str(config.model_dir)]


def test_vector_status_cli_is_json(capsys, vector_fixture):
    assert main(["vectors", "status", *_base_args(vector_fixture)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["model_version"] == "qwen3-embedding-0.6b-document-v1"
    assert payload["ready"] is True
    assert payload["active_shards"] == 0


def test_vector_status_reports_bad_manifest_as_nonzero_json(capsys, vector_fixture):
    (vector_fixture.data_dir / "manifest.json").write_text("{broken", encoding="utf-8")

    assert main(["vectors", "status", *_base_args(vector_fixture)]) != 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ready"] is False
    assert "vector command failed" in captured.err


def test_rebuild_requires_stable_generation_id_before_loading_model(capsys, vector_fixture):
    assert main([
        "vectors", "rebuild", "--target-model-version", "model-v2",
        "--generation-id", "../escape", *_base_args(vector_fixture),
    ]) != 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is False
    assert "vector command failed" in captured.err


def test_smoke_encode_missing_model_has_one_json_stdout(capsys, vector_fixture):
    assert main(["vectors", "smoke-encode", *_base_args(vector_fixture)]) != 0
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    assert json.loads(captured.out)["ok"] is False
    assert "vector command failed" in captured.err


def test_sync_uses_a_nonblocking_writer_lock_without_nested_flock(monkeypatch, capsys, vector_fixture):
    vector_fixture.model_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_sync(config, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run_id="run", vectorized_count=0, pending_count=0, watermark_ts_ms=0)

    monkeypatch.setattr("feedback_hub.vector_index.commands.sync_pending", fake_sync)

    assert main(["vectors", "sync", *_base_args(vector_fixture)]) == 0
    assert captured["nonblocking"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_nonblocking_writer_lock_reports_external_contention(tmp_path):
    lock_path = tmp_path / ".sync.lock"
    with lock_path.open("a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            with process_lock(lock_path, nonblocking=True):
                pass
