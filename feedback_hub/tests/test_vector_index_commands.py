"""Operational JSON contract for the feedback vector CLI."""
from __future__ import annotations

import json
import fcntl
import os
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
import types

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


def test_vector_status_cli_is_json(capsys, monkeypatch, vector_fixture):
    vector_fixture.model_dir.mkdir()
    manifest = ShardStore.from_config(vector_fixture).load_manifest()

    class ReadySearcher:
        def __init__(self, config):
            assert config == vector_fixture

        def ensure_ready(self):
            return manifest

    monkeypatch.setattr("feedback_hub.vector_index.commands.VectorSearcher", ReadySearcher)
    assert main(["vectors", "status", *_base_args(vector_fixture)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["model_version"] == "qwen3-embedding-0.6b-document-v1"
    assert payload["ready"] is True
    assert payload["active_shards"] == 0
    assert payload["dimension"] == 1024


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


def test_legacy_cli_help_does_not_import_vector_dependencies():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import builtins; original = builtins.__import__; "
            "builtins.__import__ = lambda name, *a, **k: (_ for _ in ()).throw(ImportError('blocked')) "
            "if name in {'numpy','fastapi','feedback_hub.vector_index.commands'} else original(name,*a,**k); "
            "from feedback_hub.cli import main; main(['--help'])"
        )],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "vectors" in result.stdout
    assert "blocked" not in result.stderr


def test_vector_parser_failure_has_one_json_stdout(capsys):
    assert main(["vectors", "search", "--limit", "0"]) != 0
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    assert json.loads(captured.out)["ok"] is False
    assert captured.err


def test_vector_search_rejects_excessive_query_work(capsys, vector_fixture):
    args = ["vectors", "search", *_base_args(vector_fixture)]
    for number in range(9):
        args.extend(["--query", f"query-{number}"])
    assert main(args) != 0
    assert json.loads(capsys.readouterr().out)["error"] == "ValueError"


def test_serve_uses_root_config_so_future_promotions_remain_visible(monkeypatch, capsys, vector_fixture):
    from feedback_hub.vector_index.commands import _serve
    calls: dict[str, object] = {}
    api = types.ModuleType("feedback_hub.vector_index.api")

    def create_app(config):
        calls["app_config"] = config
        return object()

    class Config:
        def __init__(self, app, **kwargs):
            calls["server_config"] = kwargs

    class Server:
        def __init__(self, config):
            calls["server"] = config

        def run(self):
            calls["run"] = True

    api.create_app = create_app
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.Config, uvicorn.Server = Config, Server
    monkeypatch.setitem(sys.modules, "feedback_hub.vector_index.api", api)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)

    assert _serve(vector_fixture, allow_empty_index=False) == 0
    assert calls["app_config"] == vector_fixture
    assert calls["server_config"]["host"] == "127.0.0.1"
    assert json.loads(capsys.readouterr().out)["state"] == "stopped"
