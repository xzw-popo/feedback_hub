"""Exact vector search contracts for the published feedback index."""
from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np
import pytest

from feedback_hub import db
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.models import EmbeddingRecord
from feedback_hub.vector_index.repository import VectorRepository
from feedback_hub.vector_index.search import VectorSearcher
from feedback_hub.vector_index.shards import ShardStore


class StubEncoder:
    def encode_queries(self, texts: list[str]) -> np.ndarray:
        values = {
            "工具栏": [1.0, 0.0], "全屏工具栏": [1.0, 0.0],
            "第二个": [0.0, 1.0],
        }
        return np.asarray([values[text] for text in texts], dtype=np.float32)


def _feedback(feedback_id: str, *, ts_ms: int, platform: str = "iOS",
              channel: str = "wetype", version: str = "1.2.3") -> dict[str, object]:
    return {
        "feedback_id": feedback_id, "conversation_id": feedback_id, "msg_seq": 0,
        "channel": channel, "ts_ms": ts_ms, "platform": platform, "appversion": version,
        "user_vid": "u", "service_vid": 1, "external_chat_url": None,
        "keyboard_source": "", "device_name": "", "channelid": "", "enginever": "",
        "msgtype": "text", "text": feedback_id, "tags": "", "raw_json": "{}", "pulled_at": 1,
    }


@pytest.fixture
def indexed(tmp_path):
    config = replace(
        VectorIndexConfig(), db_path=tmp_path / "feedback.db", data_dir=tmp_path / "vectors",
        dimension=2, model_version="m1", index_name="feedback-items-v1",
    )
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    rows = [
        _feedback("out-of-window", ts_ms=100),
        _feedback("in-window", ts_ms=250),
        _feedback("win", ts_ms=250, platform="Win", channel="store", version="2.0"),
        _feedback("tie-a", ts_ms=250),
        _feedback("tie-b", ts_ms=250),
    ]
    for row in rows:
        assert db.upsert_feedback(connection, row)
    connection.commit()
    repo = VectorRepository(connection)
    repo.init_schema()
    store = ShardStore.from_config(config)
    first = store.write_shard(np.asarray([[1, 0], [1, 0]], dtype=np.float32), ["out-of-window", "in-window"])
    second = store.write_shard(
        np.asarray([[0, 1], [1, 0], [1, 0]], dtype=np.float32), ["win", "tie-b", "tie-a"]
    )
    for shard, ids in ((first, ["out-of-window", "in-window"]), (second, ["win", "tie-b", "tie-a"])):
        repo.publish_shard(shard, [
            EmbeddingRecord(
                feedback_id=item_id, model_version=config.model_version,
                content_hash=hashlib.sha256(item_id.encode()).hexdigest(), shard_id=shard.shard_id,
                row_offset=offset, embedded_at_ms=1,
            ) for offset, item_id in enumerate(ids)
        ])
    connection.commit()
    manifest = store.publish_manifest([first, second], watermark_ts_ms=777)
    repo.mark_publication(
        publication_key=str(store.root), model_version=config.model_version,
        generation=manifest.generation, watermark_ts_ms=manifest.watermark_ts_ms,
    )
    connection.commit()
    yield config, connection, store
    connection.close()


@pytest.fixture
def searcher(indexed):
    config, _, _ = indexed
    return VectorSearcher(config, encoder=StubEncoder())


def test_time_filter_is_applied_before_top_k(searcher):
    hits = searcher.search(
        [{"id": "q1", "text": "全屏工具栏", "kind": "positive"}],
        {"unit": "feedback", "start_ts_ms": 200, "end_ts_ms": 300}, limit=1,
    )
    assert [hit.item_id for hit in hits] == ["in-window"]


def test_metadata_filters_are_hard_and_only_known_product_is_supported(searcher):
    result = searcher.search(
        [{"id": "q", "text": "第二个", "kind": "positive"}],
        {"unit": "feedback", "platforms": ["Win"], "channels": ["store"],
         "versions": ["2.0"], "products": ["微信输入法"]}, limit=5,
    )
    assert [hit.item_id for hit in result] == ["win"]
    with pytest.raises(ValueError, match="product"):
        searcher.search([{"id": "q", "text": "工具栏", "kind": "positive"}],
                        {"unit": "feedback", "products": ["别的产品"]}, 1)


def test_queries_are_ranked_independently_and_ties_use_item_id(searcher):
    result = searcher.search(
        [{"id": "first", "text": "工具栏", "kind": "positive"},
         {"id": "second", "text": "第二个", "kind": "negative"}],
        {"unit": "feedback", "start_ts_ms": 200, "end_ts_ms": 300}, 3,
    )
    assert [(hit.query_id, hit.item_id, hit.rank) for hit in result] == [
        ("first", "in-window", 1), ("first", "tie-a", 2), ("first", "tie-b", 3),
        ("second", "win", 1), ("second", "in-window", 2), ("second", "tie-a", 3),
    ]
    assert result.watermark_ts_ms == 777


def test_unknown_or_malformed_filters_are_rejected(searcher):
    query = [{"id": "q", "text": "工具栏", "kind": "positive"}]
    with pytest.raises(ValueError, match="unknown"):
        searcher.search(query, {"unit": "feedback", "typo": "ignored"}, 1)
    with pytest.raises(ValueError, match="unit"):
        searcher.search(query, {"unit": "conversation"}, 1)
    with pytest.raises(ValueError, match="start_ts_ms"):
        searcher.search(query, {"unit": "feedback", "start_ts_ms": True}, 1)


def test_reload_promotes_a_complete_new_manifest_without_dropping_old_state(indexed, searcher):
    config, connection, store = indexed
    old = searcher.search([{"id": "q", "text": "工具栏", "kind": "positive"}], {"unit": "feedback"}, 1)
    shard = store.write_shard(np.asarray([[0, 1]], dtype=np.float32), ["new"])
    assert db.upsert_feedback(connection, _feedback("new", ts_ms=300))
    repo = VectorRepository(connection)
    repo.publish_shard(shard, [EmbeddingRecord("new", config.model_version, hashlib.sha256(b"new").hexdigest(), shard.shard_id, 0, 1)])
    connection.commit()
    manifest = store.publish_manifest([*store.load_manifest().shards, shard], watermark_ts_ms=888)
    repo.mark_publication(
        publication_key=str(store.root), model_version=config.model_version,
        generation=manifest.generation, watermark_ts_ms=manifest.watermark_ts_ms,
    )
    connection.commit()
    assert searcher.reload_if_changed()
    assert searcher.search([{"id": "q", "text": "第二个", "kind": "positive"}], {"unit": "feedback"}, 1).watermark_ts_ms == 888
    store.manifest_path.write_text("{broken", encoding="utf-8")
    assert not searcher.reload_if_changed()
    assert searcher.search([{"id": "q", "text": "工具栏", "kind": "positive"}], {"unit": "feedback"}, 1).watermark_ts_ms == 888
    assert old.watermark_ts_ms == 777


def test_failed_reload_degrades_health_but_retains_old_state(indexed, searcher):
    _, _, store = indexed
    store.manifest_path.write_text("{broken", encoding="utf-8")

    assert not searcher.reload_if_changed()
    assert searcher.health().ready is False
    assert searcher.health().error_code == "ValueError"
    assert [hit.item_id for hit in searcher.search(
        [{"id": "q", "text": "工具栏", "kind": "positive"}], {"unit": "feedback"}, 1,
    )] == ["in-window"]


@pytest.mark.parametrize("tamper", ["duplicate", "hash", "shard", "marker"])
def test_active_generation_integrity_failure_keeps_old_state(indexed, searcher, tamper):
    config, connection, store = indexed
    old = searcher.current_manifest()
    if tamper == "duplicate":
        connection.execute(
            """INSERT INTO embedding_record
               (feedback_id, model_version, content_hash, shard_id, row_offset, embedded_at_ms)
               SELECT feedback_id, model_version, 'second-content-version', shard_id, row_offset, embedded_at_ms
               FROM embedding_record WHERE feedback_id = 'in-window'"""
        )
    elif tamper == "hash":
        connection.execute("UPDATE embedding_record SET content_hash = 'bad' WHERE feedback_id = 'in-window'")
    elif tamper == "shard":
        connection.execute("UPDATE embedding_shard SET row_count = row_count + 1")
    connection.commit()
    replacement = store.publish_manifest(old.shards, watermark_ts_ms=old.watermark_ts_ms)
    repo = VectorRepository(connection)
    if tamper != "marker":
        repo.mark_publication(
            publication_key=str(store.root), model_version=config.model_version,
            generation=replacement.generation, watermark_ts_ms=replacement.watermark_ts_ms,
        )
    connection.commit()

    assert not searcher.reload_if_changed()
    assert searcher.health().ready is False
    assert searcher.current_manifest() == old


def test_model_readiness_recovers_after_a_transient_failure(indexed):
    config, _, _ = indexed

    class FlakyEncoder:
        calls = 0

        def ensure_ready(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary model startup failure")

    searcher = VectorSearcher(config, encoder=FlakyEncoder())
    with pytest.raises(RuntimeError):
        searcher.ensure_ready()
    assert searcher.health().ready is False
    searcher.ensure_ready()
    assert searcher.health().ready is True


def test_successful_model_retry_does_not_clear_reload_corruption(indexed):
    config, _, store = indexed

    class FlakyEncoder:
        calls = 0

        def ensure_ready(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary model startup failure")

    searcher = VectorSearcher(config, encoder=FlakyEncoder())
    with pytest.raises(RuntimeError):
        searcher.ensure_ready()
    store.manifest_path.write_text("{broken", encoding="utf-8")
    assert searcher.health().error_code == "ValueError"
    with pytest.raises(RuntimeError):
        searcher.ensure_ready()
    assert searcher.health().error_code == "ValueError"
