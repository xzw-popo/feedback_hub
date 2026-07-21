"""Resumable, coordinated vector-index synchronization contracts."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from feedback_hub import db
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.repository import VectorRepository
from feedback_hub.vector_index.shards import ShardStore
from feedback_hub.vector_index.sync import rebuild_index, sync_pending


def _feedback(feedback_id: str, text: str, ts_ms: int) -> dict[str, object]:
    return {
        "feedback_id": feedback_id, "conversation_id": f"conversation-{feedback_id}",
        "msg_seq": 0, "channel": "wetype", "ts_ms": ts_ms, "platform": "iOS",
        "appversion": "1.2.3", "user_vid": "user", "service_vid": 1,
        "external_chat_url": None, "keyboard_source": "", "device_name": "",
        "channelid": "", "enginever": "", "msgtype": "text", "text": text,
        "tags": "", "raw_json": "{}", "pulled_at": 1,
    }


class DeterministicEncoder:
    def __init__(self, dimension: int = 2) -> None:
        self.dimension = dimension
        self.document_count = 0

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        self.document_count += len(texts)
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for index, text in enumerate(texts):
            vectors[index, sum(text.encode("utf-8")) % self.dimension] = 1.0
        return vectors


class FailOnceEncoder(DeterministicEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        if not self.failed:
            self.failed = True
            raise RuntimeError("encoder failed")
        return super().encode_documents(texts)


@pytest.fixture
def sync_fixture(tmp_path):
    config = replace(
        VectorIndexConfig(), db_path=tmp_path / "feedback.db", data_dir=tmp_path / "vectors",
        dimension=2, model_version="m1", shard_size=2, compact_after_shards=99,
    )
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    rows = [_feedback(f"f{index}", f"text-{index}", index) for index in range(1, 6)]
    for row in rows:
        assert db.upsert_feedback(connection, row)
    connection.execute(
        "INSERT INTO feedback_source_coverage (channel, start_ts_ms, end_ts_ms, completed_at_ms) "
        "VALUES ('wetype', 0, 5, 50)"
    )
    connection.commit()
    repo = VectorRepository(connection)
    repo.init_schema()
    store = ShardStore.from_config(config)

    class Fixture:
        pass

    fixture = Fixture()
    fixture.config, fixture.repo, fixture.shards, fixture.feedback_rows = config, repo, store, rows
    yield fixture
    repo.close()


def test_sync_retries_rows_left_unpublished_after_encoder_failure(sync_fixture):
    with pytest.raises(RuntimeError, match="encoder failed"):
        sync_pending(sync_fixture.config, encoder=FailOnceEncoder())

    assert sync_fixture.repo.pending_count("m1") == 5
    result = sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    assert result.vectorized_count == 5
    assert sync_fixture.repo.pending_count("m1") == 0
    row = sync_fixture.repo.connection.execute(
        "SELECT status, vectorized_count FROM embedding_sync_run ORDER BY started_at_ms LIMIT 1"
    ).fetchone()
    assert tuple(row) == ("failed", 0)


def test_repeated_sync_does_not_reembed_existing_ids(sync_fixture):
    encoder = DeterministicEncoder()

    sync_pending(sync_fixture.config, encoder=encoder)
    sync_pending(sync_fixture.config, encoder=encoder)

    assert encoder.document_count == len(sync_fixture.feedback_rows)


def test_max_items_is_resumable_across_multiple_chunks_and_defers_watermark(sync_fixture):
    initial = sync_fixture.shards.publish_manifest([], watermark_ts_ms=7)
    encoder = DeterministicEncoder()

    result = sync_pending(sync_fixture.config, encoder=encoder, max_items=3)

    assert result.vectorized_count == 3
    assert result.pending_count == 2
    assert encoder.document_count == 3
    assert sync_fixture.shards.load_manifest().watermark_ts_ms == initial.watermark_ts_ms
    assert len(sync_fixture.shards.load_manifest().shards) == 2

    resumed = sync_pending(sync_fixture.config, encoder=encoder)

    assert resumed.vectorized_count == 2
    assert resumed.pending_count == 0
    assert encoder.document_count == 5
    assert sync_fixture.shards.load_manifest().watermark_ts_ms == 50


def test_sync_compacts_after_configured_shard_threshold(sync_fixture):
    config = replace(sync_fixture.config, shard_size=1, compact_after_shards=2)

    sync_pending(config, encoder=DeterministicEncoder())

    assert sync_fixture.repo.status().active_shards == 1


def test_manifest_crash_after_database_commit_is_recovered_without_reembedding(sync_fixture, monkeypatch):
    original = ShardStore._replace_manifest
    crashed = False

    def fail_once(self, *args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("manifest crash")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ShardStore, "_replace_manifest", fail_once)
    encoder = DeterministicEncoder()

    with pytest.raises(RuntimeError, match="manifest crash"):
        sync_pending(sync_fixture.config, encoder=encoder, max_items=2)

    assert sync_fixture.shards.publication_journal_path.exists()
    assert sync_fixture.repo.pending_count("m1") == 3
    recovered = sync_pending(sync_fixture.config, encoder=encoder, max_items=2)

    assert recovered.vectorized_count == 2
    assert encoder.document_count == 4
    assert not sync_fixture.shards.publication_journal_path.exists()


def test_rebuild_uses_a_separate_generation_without_clearing_active_index(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())
    old_manifest = sync_fixture.shards.load_manifest()
    rebuilt_config = replace(sync_fixture.config, model_version="m2")

    result = rebuild_index(rebuilt_config, encoder=DeterministicEncoder())

    rebuilt_store = ShardStore.from_config(
        replace(rebuilt_config, data_dir=sync_fixture.config.data_dir / "generations" / "m2")
    )
    assert result.pending_count == 0
    assert sync_fixture.shards.load_manifest() == old_manifest
    assert rebuilt_store.load_manifest().model_version == "m2"


def test_rebuild_requires_a_model_version_distinct_from_the_active_index(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    with pytest.raises(ValueError, match="distinct"):
        rebuild_index(sync_fixture.config, encoder=DeterministicEncoder())
