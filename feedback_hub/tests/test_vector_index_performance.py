"""Opt-in production-path capacity baseline for exact feedback vector search."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

import numpy as np
import pytest

from feedback_hub import db
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.models import EmbeddingRecord
from feedback_hub.vector_index.repository import VectorRepository
from feedback_hub.vector_index.search import VectorSearcher
from feedback_hub.vector_index.shards import ShardStore


class BenchmarkEncoder:
    def encode_queries(self, texts):
        values = np.zeros((len(texts), 1024), dtype=np.float32)
        values[:, 0] = 1.0
        return values


def _feedback_row(index: int) -> tuple[object, ...]:
    return (
        f"f-{index:06d}", f"c-{index:06d}", 0, "wetype" if index % 2 else "store",
        1_000_000 + index, "iOS" if index % 2 else "Win", "2.0" if index % 2 else "1.0",
        "u", 1, None, "", "", "", "", "text", f"feedback {index}", "", "{}", 1,
    )


@pytest.mark.performance
def test_exact_search_127k_x_1024_production_baseline(tmp_path):
    """Exercise SQLite mappings, manifest validation, mmap reload, filters, and merge."""
    rows, dimension, limit = 127_000, 1024, 20
    config = replace(
        VectorIndexConfig(), db_path=tmp_path / "feedback.db", data_dir=tmp_path / "vectors",
        dimension=dimension, model_version="benchmark-v1", shard_size=rows,
    )
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    connection.executemany(
        "INSERT INTO feedback (feedback_id, conversation_id, msg_seq, channel, ts_ms, platform, appversion, "
        "user_vid, service_vid, external_chat_url, keyboard_source, device_name, channelid, enginever, "
        "msgtype, text, tags, raw_json, pulled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_feedback_row(index) for index in range(rows)),
    )
    connection.commit()
    repo = VectorRepository(connection)
    repo.init_schema()
    store = ShardStore.from_config(config)
    rng = np.random.default_rng(20260721)
    shards = []
    for start in range(0, rows, rows // 2):
        count = min(rows // 2, rows - start)
        values = rng.standard_normal((count, dimension), dtype=np.float32)
        values /= np.sqrt(np.einsum("nd,nd->n", values, values, dtype=np.float32))[:, None]
        ids = [f"f-{index:06d}" for index in range(start, start + count)]
        shards.append(store.write_shard(values, ids))
        repo.publish_shard(shards[-1], [
            EmbeddingRecord(
                feedback_id=item_id, model_version=config.model_version,
                content_hash=hashlib.sha256(f"feedback {index}".encode()).hexdigest(),
                shard_id=shards[-1].shard_id, row_offset=offset, embedded_at_ms=1,
            )
            for offset, (index, item_id) in enumerate(zip(range(start, start + count), ids))
        ])
        # Keep only the immutable shard metadata; the next chunk must not hold
        # a second half-corpus matrix in Python heap memory.
        del values, ids
    connection.commit()
    manifest = store.publish_manifest(shards, watermark_ts_ms=9_999_999)
    repo.mark_publication(
        publication_key=str(store.root), model_version=config.model_version,
        generation=manifest.generation, watermark_ts_ms=manifest.watermark_ts_ms,
    )
    connection.commit()

    started = time.perf_counter()
    searcher = VectorSearcher(config, encoder=BenchmarkEncoder(), score_chunk_rows=4096)
    cold_load_seconds = time.perf_counter() - started
    filters = {"unit": "feedback", "start_ts_ms": 1_010_000, "end_ts_ms": 1_120_000,
               "platforms": ["iOS", "Win"], "channels": ["wetype", "store"], "versions": ["1.0", "2.0"]}
    timings, repeated = [], []
    for _ in range(10):
        started = time.perf_counter()
        result = searcher.search(
            [{"id": "positive", "text": "query", "kind": "positive"},
             {"id": "negative", "text": "query", "kind": "negative"}], filters, limit,
        )
        timings.append((time.perf_counter() - started) * 1000)
        repeated.append([(hit.query_id, hit.item_id) for hit in result.hits])
        assert len(result.hits) == limit * 2
        assert all(np.isfinite(hit.score) for hit in result.hits)
    stats = searcher.last_search_metrics()
    assert all(ids == repeated[0] for ids in repeated)
    assert stats.eligibility_passes == len(shards)
    assert stats.max_scored_rows <= 4096
    print(json.dumps({
        "cold_load_seconds": cold_load_seconds,
        "query_p50_ms": float(np.percentile(timings, 50)),
        "query_p95_ms": float(np.percentile(timings, 95)),
        "max_scored_rows": stats.max_scored_rows,
        "eligibility_passes": stats.eligibility_passes,
    }, sort_keys=True))
    connection.close()
