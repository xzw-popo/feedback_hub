"""Operational commands for the local feedback vector index.

The command surface intentionally returns machine-readable, redacted JSON.  It
does not print model paths, shard paths, or implementation exceptions: operators
can use stderr and the dedicated runtime log for diagnostics.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import VectorIndexConfig
from .encoder import QwenEmbeddingEncoder
from .repository import VectorRepository
from .search import VectorSearcher
from .shards import ShardStore, compact_active_shards
from .sync import active_index_config, process_lock, rebuild_index, sync_pending


_MAX_SEARCH_LIMIT = 100


def _emit(payload: dict[str, Any]) -> None:
    """Write exactly one compact JSON object for every vector command."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _logical_model_version(model_version: str) -> str:
    return model_version.split("::generation::", 1)[0]


def _config_from_args(args: Any) -> VectorIndexConfig:
    base = VectorIndexConfig()
    values: dict[str, Any] = {}
    for name in (
        "db_path", "data_dir", "model_dir", "index_name", "model_version", "dimension",
        "batch_size", "max_length", "shard_size", "compact_after_shards", "host", "port",
    ):
        value = getattr(args, name, None)
        if value is not None:
            values[name] = Path(value) if name in {"db_path", "data_dir", "model_dir"} else value
    return replace(base, **values)


def _require_model(config: VectorIndexConfig) -> None:
    if not config.model_dir.is_dir():
        raise FileNotFoundError("model directory is missing")


def _validate_rebuild_request(target_model_version: str, generation_id: str) -> None:
    """Reject unsafe resume identities before touching model or index state."""
    for value, label in ((target_model_version, "model version"), (generation_id, "generation ID")):
        if (not isinstance(value, str) or not value.strip() or Path(value).name != value
                or value in {".", ".."}):
            raise ValueError(f"rebuild {label} must be a safe non-empty path component")


def _status(config: VectorIndexConfig) -> dict[str, Any]:
    active = active_index_config(config)
    store = ShardStore.from_config(active)
    manifest = store.load_manifest()
    repository = VectorRepository(active)
    try:
        repository.init_schema()
        counts = repository.status()
        pending = repository.pending_count(active.model_version)
    finally:
        repository.close()
    return {
        "ok": True,
        "ready": True,
        "index": active.index_name,
        "model_version": _logical_model_version(active.model_version),
        "generation": manifest.generation,
        "watermark_ts_ms": manifest.watermark_ts_ms,
        "active_shards": counts.active_shards,
        "retired_shards": counts.retired_shards,
        "pending_count": pending,
    }


def _result_payload(command: str, result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "run_id": result.run_id,
        "vectorized_count": result.vectorized_count,
        "pending_count": result.pending_count,
        "watermark_ts_ms": result.watermark_ts_ms,
    }


def _search_filters(args: Any) -> dict[str, Any]:
    filters: dict[str, Any] = {"unit": "feedback"}
    for argument, key in (("start_ts_ms", "start_ts_ms"), ("end_ts_ms", "end_ts_ms")):
        value = getattr(args, argument, None)
        if value is not None:
            filters[key] = value
    for argument, key in (("platform", "platforms"), ("channel", "channels"),
                          ("version", "versions"), ("product", "products")):
        values = getattr(args, argument, None)
        if values:
            filters[key] = values
    return filters


def _search(config: VectorIndexConfig, args: Any) -> dict[str, Any]:
    _require_model(config)
    active = active_index_config(config)
    queries = [
        {"id": f"positive-{position}", "text": text, "kind": "positive"}
        for position, text in enumerate(args.query or [], start=1)
    ] + [
        {"id": f"negative-{position}", "text": text, "kind": "negative"}
        for position, text in enumerate(args.negative_query or [], start=1)
    ]
    if not queries:
        raise ValueError("search requires at least one --query or --negative-query")
    searcher = VectorSearcher(active)
    searcher.ensure_ready()
    result = searcher.search(queries, _search_filters(args), args.limit)
    return {
        "ok": True,
        "index": active.index_name,
        "watermark_ts_ms": result.watermark_ts_ms,
        "hit_count": len(result.hits),
        "hits": [hit.to_dict() for hit in result.hits],
    }


def _serve(config: VectorIndexConfig, *, allow_empty_index: bool) -> int:
    if not allow_empty_index:
        _require_model(config)
    active = active_index_config(config)
    watermark = 0
    if not allow_empty_index:
        searcher = VectorSearcher(active)
        watermark = searcher.ensure_ready().watermark_ts_ms
    _emit({
        "ok": True, "index": active.index_name,
        "model_version": _logical_model_version(active.model_version),
        "watermark_ts_ms": watermark,
        "host": "127.0.0.1", "port": config.port,
        "allow_empty_index": allow_empty_index,
    })
    import uvicorn
    from .api import create_app
    # The command is loopback-only even if a caller supplies a different host.
    uvicorn.run(create_app(active), host="127.0.0.1", port=config.port, reload=False, log_level="info")
    return 0


def cmd_vectors(args: Any) -> int:
    """Route vector operations while preserving a one-object stdout contract."""
    config = _config_from_args(args)
    try:
        command = args.vector_command
        if command == "status":
            _emit(_status(config))
            return 0
        if command == "sync":
            _require_model(config)
            result = sync_pending(config, max_items=args.max_items, nonblocking=True)
            _emit(_result_payload(command, result))
            return 0
        if command == "rebuild":
            _validate_rebuild_request(args.target_model_version, args.generation_id)
            _require_model(config)
            result = rebuild_index(
                config, target_model_version=args.target_model_version,
                target_generation_id=args.generation_id, nonblocking=True,
            )
            payload = _result_payload(command, result)
            if result.pending_count:
                payload["ok"] = False
                _emit(payload)
                print("vector command failed: incomplete rebuild", file=sys.stderr)
                return 1
            _emit(payload)
            return 0
        if command == "compact":
            with process_lock(config.data_dir / ".sync.lock", nonblocking=True):
                active = active_index_config(config, _lock_held=True)
                repository = VectorRepository(active)
                try:
                    repository.init_schema()
                    store = ShardStore.from_config(active)
                    manifest = compact_active_shards(repository, store)
                finally:
                    repository.close()
            _emit({"ok": True, "index": active.index_name, "generation": manifest.generation,
                   "watermark_ts_ms": manifest.watermark_ts_ms, "active_shards": len(manifest.shards)})
            return 0
        if command == "search":
            _emit(_search(config, args))
            return 0
        if command == "serve":
            return _serve(config, allow_empty_index=args.allow_empty_index)
        if command == "smoke-encode":
            _require_model(config)
            encoder = QwenEmbeddingEncoder(config)
            vectors = encoder.encode_documents([args.text])
            _emit({"ok": True, "dimension": int(vectors.shape[1]), "count": int(vectors.shape[0])})
            return 0
        raise ValueError("unknown vector command")
    except Exception as error:
        print(f"vector command failed: {type(error).__name__}", file=sys.stderr)
        command = getattr(args, "vector_command", "unknown")
        payload = {"ok": False, "command": command, "error": type(error).__name__}
        if command == "status":
            payload["ready"] = False
        _emit(payload)
        return 1
