"""Crash-safe, resumable synchronization of feedback embeddings.

All reader-visible changes flow through :meth:`ShardStore.publish_with_database`.
The durable publication journal is therefore always written before SQLite changes
and a manifest is never switched until the corresponding record mappings commit.
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Sequence

from .config import VectorIndexConfig
from .encoder import EmbeddingEncoder, QwenEmbeddingEncoder
from .models import EmbeddingRecord, PendingFeedback
from .repository import VectorRepository, now_ms
from .shards import ShardManifest, ShardStore, compact_active_shards


@dataclass(frozen=True)
class SyncResult:
    run_id: str
    vectorized_count: int
    pending_count: int
    watermark_ts_ms: int


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    """Serialize writers across processes for this index root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _current_manifest(store: ShardStore) -> ShardManifest | None:
    try:
        return store.load_manifest()
    except FileNotFoundError:
        return None


def _manifest_remap(
    repository: VectorRepository, manifest: ShardManifest | None
) -> list[dict[str, object]]:
    """Materialize the exact database mapping represented by a manifest."""
    if manifest is None:
        return []
    remap: list[dict[str, object]] = []
    for shard in manifest.shards:
        rows = repository.connection.execute(
            """SELECT feedback_id, model_version, content_hash, row_offset
               FROM embedding_record WHERE shard_id = ? ORDER BY row_offset""",
            (shard.shard_id,),
        ).fetchall()
        if len(rows) != shard.row_count or [int(row[3]) for row in rows] != list(range(shard.row_count)):
            raise ValueError("database mappings do not exactly cover an active shard")
        remap.extend({
            "feedback_id": str(row[0]), "model_version": str(row[1]),
            "content_hash": str(row[2]), "shard_id": shard.shard_id,
            "row_offset": int(row[3]),
        } for row in rows)
    return remap


def _records_for(
    rows: Sequence[PendingFeedback], *, model_version: str, shard_id: str
) -> list[EmbeddingRecord]:
    embedded_at_ms = now_ms()
    return [
        EmbeddingRecord(
            feedback_id=row.feedback_id, model_version=model_version,
            content_hash=row.content_hash, shard_id=shard_id, row_offset=index,
            embedded_at_ms=embedded_at_ms,
        )
        for index, row in enumerate(rows)
    ]


def _records_remap(records: Sequence[EmbeddingRecord]) -> list[dict[str, object]]:
    return [
        {
            "feedback_id": record.feedback_id, "model_version": record.model_version,
            "content_hash": record.content_hash, "shard_id": record.shard_id,
            "row_offset": record.row_offset,
        }
        for record in records
    ]


def _publish_chunk(
    repository: VectorRepository,
    store: ShardStore,
    rows: Sequence[PendingFeedback],
    vectors,
    *,
    watermark_ts_ms: int,
) -> ShardManifest:
    """Publish one completed chunk through the shared durable transaction protocol."""
    shard = store.write_shard(vectors, [row.feedback_id for row in rows])
    records = _records_for(rows, model_version=store.model_version, shard_id=shard.shard_id)
    old = _current_manifest(store)
    old_remap = _manifest_remap(repository, old)
    new = store.manifest_for(
        [*(old.shards if old is not None else ()), shard],
        watermark_ts_ms=watermark_ts_ms,
    )
    remap = [*old_remap, *_records_remap(records)]

    def database_mutation() -> None:
        repository._insert_shard(shard)
        for record in records:
            repository._insert_record(record)

    return store.publish_with_database(
        repository, old_manifest=old, new_manifest=new, new_shards=[shard],
        row_remap=remap, database_mutation=database_mutation,
    )


def _publish_watermark_if_needed(
    repository: VectorRepository, store: ShardStore, *, watermark_ts_ms: int
) -> ShardManifest:
    old = _current_manifest(store)
    if old is not None and old.watermark_ts_ms == watermark_ts_ms:
        return old
    shards = old.shards if old is not None else ()
    new = store.manifest_for(shards, watermark_ts_ms=watermark_ts_ms)
    return store.publish_with_database(
        repository, old_manifest=old, new_manifest=new, new_shards=[],
        row_remap=_manifest_remap(repository, old), database_mutation=lambda: None,
    )


def _sync(
    config: VectorIndexConfig,
    *,
    run_type: str,
    encoder: EmbeddingEncoder | None,
    max_items: int | None,
) -> SyncResult:
    if max_items is not None and max_items <= 0:
        raise ValueError("max_items must be positive when supplied")
    with process_lock(config.data_dir / ".sync.lock"):
        repository = VectorRepository(config)
        run_id = ""
        vectorized_count = 0
        try:
            repository.init_schema()
            store = ShardStore.from_config(config)
            run_id = repository.begin_run(run_type=run_type, model_version=config.model_version)
            active_encoder = encoder
            remaining_budget = max_items
            manifest = _current_manifest(store)
            while remaining_budget is None or remaining_budget > 0:
                limit = config.shard_size if remaining_budget is None else min(config.shard_size, remaining_budget)
                rows = repository.pending_feedback(config.model_version, limit)
                if not rows:
                    break
                if active_encoder is None:
                    active_encoder = QwenEmbeddingEncoder(config)
                vectors = active_encoder.encode_documents([row.text for row in rows])
                previous_watermark = manifest.watermark_ts_ms if manifest is not None else 0
                manifest = _publish_chunk(
                    repository, store, rows, vectors, watermark_ts_ms=previous_watermark
                )
                vectorized_count += len(rows)
                if remaining_budget is not None:
                    remaining_budget -= len(rows)
                if len(manifest.shards) >= config.compact_after_shards:
                    manifest = compact_active_shards(repository, store)

            pending_count = repository.pending_count(config.model_version)
            if pending_count == 0:
                manifest = _publish_watermark_if_needed(
                    repository, store, watermark_ts_ms=repository.source_watermark_ms()
                )
            elif manifest is None:
                # A partial run with no completed rows has no reader-visible index.
                # Its result reports the conservative zero watermark.
                manifest = ShardManifest(1, 0, config.model_version, config.dimension, 0, ())
            status = "succeeded" if pending_count == 0 else "partial"
            repository.finish_run(run_id, status=status, vectorized_count=vectorized_count)
            return SyncResult(run_id, vectorized_count, pending_count, manifest.watermark_ts_ms)
        except Exception as exc:
            if run_id:
                repository.finish_run(
                    run_id, status="failed", vectorized_count=vectorized_count,
                    error_code=type(exc).__name__,
                )
            raise
        finally:
            repository.close()


def sync_pending(
    config: VectorIndexConfig, *, encoder: EmbeddingEncoder | None = None,
    max_items: int | None = None,
) -> SyncResult:
    """Embed missing exact feedback IDs in shard-sized, restartable chunks."""
    return _sync(config, run_type="incremental", encoder=encoder, max_items=max_items)


def _rebuild_generation_config(config: VectorIndexConfig) -> VectorIndexConfig:
    model_version = config.model_version.strip()
    if not model_version or Path(model_version).name != model_version or model_version in {".", ".."}:
        raise ValueError("rebuild requires an explicit safe model_version")
    active_manifest_path = config.data_dir / "manifest.json"
    if active_manifest_path.exists():
        try:
            active_model_version = json.loads(active_manifest_path.read_text(encoding="utf-8"))["model_version"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("active manifest is invalid; cannot safely rebuild") from exc
        if active_model_version == model_version:
            raise ValueError("rebuild requires a model_version distinct from the active index")
    return replace(config, data_dir=config.data_dir / "generations" / model_version)


def rebuild_index(
    config: VectorIndexConfig, *, encoder: EmbeddingEncoder | None = None,
) -> SyncResult:
    """Build a model-versioned generation without disturbing the active index.

    Each chunk uses the same coordinated journaled publication as incremental
    sync, but the manifest lives below ``generations/<model_version>``.  The
    configured active root is consequently untouched until a later explicit
    promotion operation chooses this completed generation.
    """
    return _sync(
        _rebuild_generation_config(config), run_type="rebuild", encoder=encoder,
        max_items=None,
    )
