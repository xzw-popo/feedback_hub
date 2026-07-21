"""Durable NumPy shard and manifest contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest

from feedback_hub import db
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.models import EmbeddingRecord
from feedback_hub.vector_index.models import ShardMetadata
from feedback_hub.vector_index.repository import VectorRepository
from feedback_hub.vector_index.shards import ShardStore, compact_active_shards


def normalized(rows: list[list[float]]) -> np.ndarray:
    values = np.asarray(rows, dtype=np.float32)
    return values / np.linalg.norm(values, axis=1, keepdims=True)


def test_partial_shard_never_appears_in_manifest(tmp_path):
    store = ShardStore(tmp_path, dimension=1024, model_version="m1")

    with pytest.raises(ValueError, match="non-finite"):
        store.write_shard(np.full((1, 1024), np.nan, dtype=np.float32), ["f1"])

    assert not (tmp_path / "manifest.json").exists()
    assert list((tmp_path / "shards").glob("*.npy")) == []


def test_manifest_switch_is_generation_atomic(tmp_path):
    store = ShardStore(tmp_path, dimension=2, model_version="m1")
    first = store.write_shard(normalized([[1, 0]]), ["f1"])
    initial = store.publish_manifest([first], watermark_ts_ms=10)
    second = store.write_shard(normalized([[0, 1]]), ["f2"])
    current = store.publish_manifest([first, second], watermark_ts_ms=20)

    manifest = store.load_manifest()

    assert initial.generation == 1
    assert current.generation == 2
    assert manifest.watermark_ts_ms == 20
    assert [shard.shard_id for shard in manifest.shards] == [first.shard_id, second.shard_id]


def test_manifest_uses_relocatable_safe_shard_paths(tmp_path):
    store = ShardStore(tmp_path / "original", dimension=2, model_version="m1")
    shard = store.write_shard(normalized([[1, 0]]), ["f1"])
    store.publish_manifest([shard], watermark_ts_ms=1)

    payload = json.loads(store.manifest_path.read_text(encoding="utf-8"))

    assert payload["shards"][0]["path"] == f"shards/{store.shard_path(shard).name}"
    assert not payload["shards"][0]["path"].startswith("/")


def test_load_manifest_rejects_checksum_tampering(tmp_path):
    store = ShardStore(tmp_path, dimension=2, model_version="m1")
    shard = store.write_shard(normalized([[1, 0]]), ["f1"])
    store.publish_manifest([shard], watermark_ts_ms=1)
    store.shard_path(shard).write_bytes(b"not an npy file")

    with pytest.raises(ValueError, match="checksum"):
        store.load_manifest()


def test_manifest_never_switches_to_an_unverified_shard(tmp_path):
    store = ShardStore(tmp_path, dimension=2, model_version="m1")
    first = store.write_shard(normalized([[1, 0]]), ["f1"])
    store.publish_manifest([first], watermark_ts_ms=1)
    invalid = ShardMetadata(
        shard_id="f" * 24, model_version="m1",
        path=str(store.shards_dir / "shard-ffffffffffffffff.npy"), dimension=2,
        row_count=1, checksum="f" * 64,
    )

    with pytest.raises(ValueError, match="missing"):
        store.publish_manifest([invalid], watermark_ts_ms=2)

    assert [item.shard_id for item in store.load_manifest().shards] == [first.shard_id]


@pytest.fixture
def vector_fixture(tmp_path):
    config = replace(
        VectorIndexConfig(),
        db_path=tmp_path / "feedback.db",
        data_dir=tmp_path / "vector-index",
        dimension=2,
        model_version="m1",
    )
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    for index, feedback_id in enumerate(("f1", "f2", "f3"), start=1):
        assert db.upsert_feedback(connection, {
            "feedback_id": feedback_id, "conversation_id": feedback_id, "msg_seq": 0,
            "channel": "wetype", "ts_ms": index, "platform": "iOS", "appversion": "1",
            "user_vid": "u", "service_vid": 1, "external_chat_url": None, "keyboard_source": "",
            "device_name": "", "channelid": "", "enginever": "", "msgtype": "text",
            "text": feedback_id, "tags": "", "raw_json": "{}", "pulled_at": 1,
        })
    connection.commit()
    repo = VectorRepository(connection)
    repo.init_schema()
    store = ShardStore.from_config(config)
    first = store.write_shard(normalized([[1, 0], [1, 1]]), ["f1", "f2"])
    second = store.write_shard(normalized([[0, 1]]), ["f3"])
    records = []
    for shard, ids in ((first, ["f1", "f2"]), (second, ["f3"])):
        for offset, feedback_id in enumerate(ids):
            records.append(EmbeddingRecord(
                feedback_id=feedback_id, model_version=config.model_version,
                content_hash=hashlib.sha256(feedback_id.encode()).hexdigest(),
                shard_id=shard.shard_id, row_offset=offset, embedded_at_ms=1,
            ))
        repo.publish_shard(shard, records[-len(ids):])
    store.publish_manifest([first, second], watermark_ts_ms=10)

    class Fixture:
        expected_new_generation = 2

        def active_shard_paths(self):
            return [store.shard_path(shard) for shard in store.load_manifest().shards]

        def fail_publication_after_database_commit(self):
            original = store._replace_manifest
            store._replace_manifest = lambda manifest: (_ for _ in ()).throw(RuntimeError("stop"))
            try:
                with pytest.raises(RuntimeError, match="stop"):
                    compact_active_shards(repo, store)
            finally:
                store._replace_manifest = original

    fixture = Fixture()
    fixture.config, fixture.repo, fixture.store = config, repo, store
    yield fixture
    repo.close()


def test_compaction_preserves_vectors_and_retires_old_shards_after_switch(vector_fixture):
    before = np.vstack([np.load(path, mmap_mode="r") for path in vector_fixture.active_shard_paths()])

    compact_active_shards(vector_fixture.repo, vector_fixture.store)

    after = np.vstack([np.load(path, mmap_mode="r") for path in vector_fixture.active_shard_paths()])
    np.testing.assert_allclose(after, before)
    assert vector_fixture.repo.status().active_shards == 1
    assert vector_fixture.repo.status().retired_shards == 2
    rows = vector_fixture.repo.connection.execute(
        "SELECT feedback_id, row_offset FROM embedding_record ORDER BY row_offset"
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("f1", 0), ("f2", 1), ("f3", 2)]


def test_startup_rolls_forward_database_committed_manifest_pending_publication(vector_fixture):
    vector_fixture.fail_publication_after_database_commit()

    recovered = ShardStore.open(vector_fixture.config)

    assert recovered.load_manifest().generation == vector_fixture.expected_new_generation
    assert not recovered.publication_journal_path.exists()


def test_recovery_rolls_back_uncommitted_publication_and_discovers_only_true_orphans(vector_fixture):
    store = vector_fixture.store
    orphan = store.write_shard(normalized([[1, 0]]), ["orphan"])
    old = store.load_manifest()
    replacement = store.write_shard(normalized([[-1, 0]]), ["replacement"])
    new = store.manifest_for([replacement], watermark_ts_ms=old.watermark_ts_ms)
    store.write_publication_journal(old, new, [replacement], [])

    recovered = ShardStore.open(vector_fixture.config)

    assert [item.shard_id for item in recovered.load_manifest().shards] == [item.shard_id for item in old.shards]
    assert not recovered.shard_path(replacement).exists()
    assert recovered.find_orphans(vector_fixture.repo) == [recovered.shard_path(orphan)]
