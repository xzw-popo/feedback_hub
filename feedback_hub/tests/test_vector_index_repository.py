"""Vector index SQLite metadata repository tests."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from feedback_hub import db
from feedback_hub.vector_index.models import EmbeddingRecord, ShardMetadata
from feedback_hub.vector_index.repository import VectorRepository


MODEL_VERSION = "qwen3-embedding-0.6b-document-v1"


def feedback(feedback_id: str, text: str, *, ts_ms: int = 1) -> dict[str, object]:
    return {
        "feedback_id": feedback_id,
        "conversation_id": f"conversation-{feedback_id}",
        "msg_seq": 0,
        "channel": "wetype",
        "ts_ms": ts_ms,
        "platform": "iOS",
        "appversion": "1.2.3",
        "user_vid": "user-1",
        "service_vid": 10000,
        "external_chat_url": None,
        "keyboard_source": "",
        "device_name": "",
        "channelid": "",
        "enginever": "",
        "msgtype": "text",
        "text": text,
        "tags": "",
        "raw_json": "{}",
        "pulled_at": 1,
    }


def shard(shard_id: str) -> ShardMetadata:
    return ShardMetadata(
        shard_id=shard_id,
        model_version=MODEL_VERSION,
        path=f"/vectors/{shard_id}.npy",
        dimension=1024,
        row_count=1,
        checksum=f"checksum-{shard_id}",
    )


def record(feedback_id: str, shard_id: str, row_offset: int) -> EmbeddingRecord:
    return EmbeddingRecord(
        feedback_id=feedback_id,
        model_version=MODEL_VERSION,
        content_hash=hashlib.sha256(
            ("相同文字" if feedback_id in {"f1", "f2"} else "正文").encode("utf-8")
        ).hexdigest(),
        shard_id=shard_id,
        row_offset=row_offset,
        embedded_at_ms=123,
    )


@pytest.fixture
def repository_fixture(tmp_path):
    repositories: list[VectorRepository] = []

    def create(*, feedback_rows: list[dict[str, object]]) -> VectorRepository:
        connection = db.connect(tmp_path / "feedback.db")
        db.init_schema(connection)
        for row in feedback_rows:
            assert db.upsert_feedback(connection, row)
        connection.commit()
        repository = VectorRepository(connection)
        repository.init_schema()
        repositories.append(repository)
        return repository

    yield create

    for repository in repositories:
        repository.close()


def test_pending_feedback_is_feedback_id_idempotent(repository_fixture):
    repo = repository_fixture(feedback_rows=[
        feedback("f1", "相同文字"), feedback("f2", "相同文字"),
    ])

    pending = repo.pending_feedback(MODEL_VERSION, 10)

    assert [row.feedback_id for row in pending] == ["f1", "f2"]
    assert pending[0].content_hash == hashlib.sha256("相同文字".encode("utf-8")).hexdigest()

    repo.publish_shard(shard("s1"), [record("f1", "s1", 0)])

    assert [row.feedback_id for row in repo.pending_feedback(MODEL_VERSION, 10)] == ["f2"]


def test_publish_shard_and_records_commit_atomically(repository_fixture, monkeypatch):
    repo = repository_fixture(feedback_rows=[feedback("f1", "正文")])
    monkeypatch.setattr(
        repo,
        "_insert_record",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        repo.publish_shard(shard("s1"), [record("f1", "s1", 0)])

    assert repo.status().active_shards == 0
    assert repo.connection.execute("SELECT COUNT(*) FROM embedding_record").fetchone()[0] == 0


def test_init_schema_creates_metadata_tables(repository_fixture):
    repo = repository_fixture(feedback_rows=[])

    names = {
        row[0]
        for row in repo.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert {"embedding_shard", "embedding_record", "embedding_sync_run"} <= names


def test_run_lifecycle_is_persisted(repository_fixture):
    repo = repository_fixture(feedback_rows=[])

    run_id = repo.begin_run(run_type="sync", model_version=MODEL_VERSION)
    repo.finish_run(run_id, status="succeeded", vectorized_count=3)

    row = repo.connection.execute(
        "SELECT status, vectorized_count, error_code, finished_at_ms "
        "FROM embedding_sync_run WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert tuple(row)[:3] == ("succeeded", 3, "")
    assert row[3] is not None


def test_pending_feedback_skips_blank_text_and_orders_stably(repository_fixture):
    repo = repository_fixture(feedback_rows=[
        feedback("f2", "later", ts_ms=2),
        feedback("f3", "   ", ts_ms=1),
        feedback("f1", "first", ts_ms=1),
    ])

    assert [row.feedback_id for row in repo.pending_feedback(MODEL_VERSION, 10)] == ["f1", "f2"]
