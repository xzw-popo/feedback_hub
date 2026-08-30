"""Crash-safe, resumable synchronization of feedback embeddings.

All reader-visible changes flow through :meth:`ShardStore.publish_with_database`.
The durable publication journal is therefore always written before SQLite changes
and a manifest is never switched until the corresponding record mappings commit.
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Sequence

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


PROMOTION_SCHEMA_VERSION = 1
_UNSET = object()


@dataclass(frozen=True)
class GenerationSpec:
    logical_model_version: str
    generation_id: str
    storage_model_version: str
    config: VectorIndexConfig


@contextmanager
def process_lock(path: Path, *, nonblocking: bool = False) -> Iterator[None]:
    """Serialize writers across processes for this index root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        fcntl.flock(handle.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def source_ingestion_lock(config: VectorIndexConfig) -> Iterator[None]:
    """Lock the final source snapshot against compliant feedback ingestion.

    Producers must hold this lock while committing ``feedback`` and
    ``feedback_source_coverage`` together.  The upcoming pull pipeline can
    import this public context manager; SQLite's immediate transaction remains
    a second line of defence for any normal SQLite writer.
    """
    return process_lock(config.data_dir / ".source-ingestion.lock")


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("vector generation path contains a symlink")


def _validate_index_root(config: VectorIndexConfig) -> Path:
    root = config.data_dir.absolute()
    _reject_symlink(root)
    return root


def _validated_generation_path(base_config: VectorIndexConfig, generation_id: str) -> Path:
    root = _validate_index_root(base_config)
    if not generation_id or Path(generation_id).name != generation_id or generation_id in {".", ".."}:
        raise ValueError("generation_id must be a safe non-empty path component")
    generations = root / "generations"
    target = generations / generation_id
    _reject_symlink(generations)
    _reject_symlink(target)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("rebuild generation escapes active index root") from exc
    return target


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


def _atomic_json_replace(
    path: Path, payload: dict[str, object], *, expected: object = _UNSET,
) -> None:
    actual = _read_json(path) if path.exists() else None
    if expected is not _UNSET and actual != expected:
        raise RuntimeError("active-generation pointer changed before publication")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("invalid active-generation metadata") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid active-generation metadata")
    return payload


def _pointer_paths(config: VectorIndexConfig) -> tuple[Path, Path]:
    return (
        config.data_dir / "active-generation.json",
        config.data_dir / "generation-promotion-journal.json",
    )


def _pointer_payload(base_config: VectorIndexConfig, spec: GenerationSpec) -> dict[str, object]:
    relative = _validated_generation_path(base_config, spec.generation_id).relative_to(
        _validate_index_root(base_config)
    )
    return {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "model_version": spec.logical_model_version,
        "generation_id": spec.generation_id,
        "storage_model_version": spec.storage_model_version,
        "data_dir": relative.as_posix(),
    }


def _config_from_pointer(base_config: VectorIndexConfig, payload: dict[str, object]) -> VectorIndexConfig:
    if payload.get("schema_version") != PROMOTION_SCHEMA_VERSION:
        raise ValueError("unknown active-generation schema version")
    model_version = payload.get("model_version")
    generation_id = payload.get("generation_id")
    storage_model_version = payload.get("storage_model_version")
    raw_path = payload.get("data_dir")
    if (not isinstance(model_version, str) or not isinstance(generation_id, str)
            or not isinstance(storage_model_version, str) or not isinstance(raw_path, str)
            or Path(raw_path).is_absolute()):
        raise ValueError("invalid active-generation metadata")
    candidate = _validated_generation_path(base_config, generation_id)
    if candidate.relative_to(_validate_index_root(base_config)).as_posix() != raw_path:
        raise ValueError("active generation path does not match generation_id")
    if storage_model_version != _storage_model_version(model_version, generation_id):
        raise ValueError("active generation storage identity is invalid")
    try:
        candidate.resolve().relative_to(_validate_index_root(base_config).resolve())
    except ValueError as exc:
        raise ValueError("active generation escapes index root") from exc
    return replace(base_config, model_version=storage_model_version, data_dir=candidate)


def _recover_active_generation(base_config: VectorIndexConfig) -> None:
    pointer_path, journal_path = _pointer_paths(base_config)
    if not journal_path.exists():
        return
    journal = _read_json(journal_path)
    if journal.get("schema_version") != PROMOTION_SCHEMA_VERSION:
        raise ValueError("unknown generation promotion journal schema version")
    target = journal.get("target")
    expected_previous = journal.get("expected_previous", _UNSET)
    if (not isinstance(target, dict)
            or (expected_previous is not None and not isinstance(expected_previous, dict))
            or expected_previous is _UNSET):
        raise ValueError("invalid generation promotion journal")
    previous = _read_json(pointer_path) if pointer_path.exists() else None
    if previous == target:
        journal_path.unlink(missing_ok=True)
        return
    if previous != expected_previous:
        # A later promotion won.  A stale journal must never resurrect its
        # target over the live pointer; remove only the obsolete intent.
        journal_path.unlink(missing_ok=True)
        return
    target_config = _config_from_pointer(base_config, target)
    target_store = ShardStore.from_config(target_config)
    target_store.load_manifest()
    _atomic_json_replace(pointer_path, target, expected=expected_previous)
    journal_path.unlink(missing_ok=True)


def active_index_config(config: VectorIndexConfig, *, _lock_held: bool = False) -> VectorIndexConfig:
    """Resolve the crash-safe active-generation pointer, or the root legacy index."""
    _validate_index_root(config)
    if not _lock_held:
        with process_lock(config.data_dir / ".sync.lock"):
            return active_index_config(config, _lock_held=True)
    _recover_active_generation(config)
    pointer_path, _ = _pointer_paths(config)
    if not pointer_path.exists():
        return config
    return _config_from_pointer(config, _read_json(pointer_path))


def _promote_generation(
    base_config: VectorIndexConfig, spec: GenerationSpec,
    repository: VectorRepository, store: ShardStore, *, pending_count: int,
) -> None:
    if pending_count != 0:
        raise ValueError("cannot promote an incomplete rebuild")
    # Re-check the exact model while the source-ingestion lock is still held.
    pending, _ = repository.pending_and_coverage_snapshot(spec.storage_model_version)
    if pending != 0:
        raise ValueError("cannot promote a rebuild with pending feedback")
    store.load_manifest()
    pointer_path, journal_path = _pointer_paths(base_config)
    target = _pointer_payload(base_config, spec)
    previous = _read_json(pointer_path) if pointer_path.exists() else None
    _atomic_json_replace(
        journal_path,
        {
            "schema_version": PROMOTION_SCHEMA_VERSION,
            "expected_previous": previous,
            "target": target,
        },
    )
    _atomic_json_replace(pointer_path, target, expected=previous)
    journal_path.unlink(missing_ok=True)


def _sync(
    config: VectorIndexConfig,
    *,
    run_type: str,
    encoder: EmbeddingEncoder | None,
    max_items: int | None,
    lock_path: Path | None = None,
    source_lock_config: VectorIndexConfig | None = None,
    on_complete: Callable[[VectorRepository, ShardStore, int], None] | None = None,
    assume_writer_lock: bool = False,
) -> SyncResult:
    if max_items is not None and max_items <= 0:
        raise ValueError("max_items must be positive when supplied")
    lock = nullcontext() if assume_writer_lock else process_lock(lock_path or config.data_dir / ".sync.lock")
    with lock:
        repository = VectorRepository(config)
        run_id = ""
        vectorized_count = 0
        try:
            repository.init_schema()
            run_id = repository.begin_run(run_type=run_type, model_version=config.model_version)
            # A run exists before opening/recovering the durable generation, so
            # corrupt journals/manifests are audited as failed runs.
            store = ShardStore.from_config(config)
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
            with source_ingestion_lock(source_lock_config or config):
                pending_count, coverage_watermark = repository.pending_and_coverage_snapshot(
                    config.model_version
                )
                if pending_count == 0:
                    manifest = _publish_watermark_if_needed(
                        repository, store, watermark_ts_ms=coverage_watermark
                    )
                elif manifest is None:
                    # A partial run with no completed rows has no reader-visible index.
                    # Its result reports the conservative zero watermark.
                    manifest = ShardManifest(1, 0, config.model_version, config.dimension, 0, ())
                # Retrying an empty/resume run must still compact stranded small
                # shards from a prior interrupted process.
                if manifest is not None and len(manifest.shards) >= config.compact_after_shards:
                    manifest = compact_active_shards(repository, store)
                if on_complete is not None:
                    on_complete(repository, store, pending_count)
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


def _record_preopen_failure(
    config: VectorIndexConfig, *, run_type: str, model_version: str, error: Exception
) -> None:
    """Audit failures while resolving an active-generation pointer before open."""
    repository = VectorRepository(config)
    try:
        repository.init_schema()
        run_id = repository.begin_run(run_type=run_type, model_version=model_version)
        repository.finish_run(
            run_id, status="failed", vectorized_count=0, error_code=type(error).__name__,
        )
    finally:
        repository.close()


def sync_pending(
    config: VectorIndexConfig, *, encoder: EmbeddingEncoder | None = None,
    max_items: int | None = None, nonblocking: bool = False,
) -> SyncResult:
    """Embed missing exact feedback IDs in shard-sized, restartable chunks."""
    _validate_index_root(config)
    with process_lock(config.data_dir / ".sync.lock", nonblocking=nonblocking):
        try:
            active = active_index_config(config, _lock_held=True)
        except Exception as exc:
            _record_preopen_failure(
                config, run_type="incremental", model_version=config.model_version, error=exc
            )
            raise
        return _sync(
            active, run_type="incremental", encoder=encoder, max_items=max_items,
            source_lock_config=config, assume_writer_lock=True,
        )


def _storage_model_version(model_version: str, generation_id: str) -> str:
    return f"{model_version}::generation::{generation_id}"


def _rebuild_generation_spec(
    config: VectorIndexConfig, target_model_version: str, target_generation_id: str,
) -> GenerationSpec:
    model_version = target_model_version.strip()
    if not model_version or Path(model_version).name != model_version or model_version in {".", ".."}:
        raise ValueError("rebuild requires an explicit safe model_version")
    if not isinstance(target_generation_id, str):
        raise ValueError("generation_id must be a safe non-empty path component")
    generation_id = target_generation_id
    data_dir = _validated_generation_path(config, generation_id)
    return GenerationSpec(
        logical_model_version=model_version, generation_id=generation_id,
        storage_model_version=_storage_model_version(model_version, generation_id),
        config=replace(config, model_version=_storage_model_version(model_version, generation_id), data_dir=data_dir),
    )


def rebuild_index(
    config: VectorIndexConfig, *, target_model_version: str,
    target_generation_id: str,
    encoder: EmbeddingEncoder | None = None,
    nonblocking: bool = False,
) -> SyncResult:
    """Rebuild and atomically promote an explicit, distinct target model.

    Chunks checkpoint under ``generations/<target_model_version>`` while the
    root index remains readable.  Only a fully covered generation is promoted
    through the crash-recoverable active-generation pointer.
    """
    _validate_index_root(config)
    spec = _rebuild_generation_spec(config, target_model_version, target_generation_id)
    with process_lock(config.data_dir / ".sync.lock", nonblocking=nonblocking):
        try:
            active_index_config(config, _lock_held=True)
            pointer_path, _ = _pointer_paths(config)
            active_logical = (
                str(_read_json(pointer_path)["model_version"])
                if pointer_path.exists() else config.model_version
            )
            if active_logical == spec.logical_model_version:
                raise ValueError("rebuild requires a model_version distinct from the active index")
        except Exception as exc:
            _record_preopen_failure(
                config, run_type="rebuild", model_version=target_model_version, error=exc
            )
            raise
        return _sync(
            spec.config, run_type="rebuild", encoder=encoder, max_items=None,
            source_lock_config=config, assume_writer_lock=True,
            on_complete=lambda repository, store, pending: _promote_generation(
                config, spec, repository, store, pending_count=pending
            ),
        )
