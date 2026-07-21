"""Memory-mapped exact cosine search for the active feedback generation.

The synchronizer is the only writer.  This module treats each published
manifest as immutable: a complete candidate state is validated off to the
side, then one reference swap makes it visible to new requests.
"""
from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .config import VectorIndexConfig
from .models import ShardMetadata
from .repository import VectorRepository
from .shards import ShardManifest, ShardStore
from .sync import active_index_config


_FILTER_KEYS = frozenset({
    "unit", "start_ts_ms", "end_ts_ms", "platforms", "channels", "products", "versions",
})
_SUPPORTED_PRODUCT = "微信输入法"


@dataclass(frozen=True)
class VectorHit:
    item_id: str
    query_id: str
    score: float
    rank: int

    def to_dict(self) -> dict[str, object]:
        return {"item_id": self.item_id, "query_id": self.query_id, "score": self.score, "rank": self.rank}


@dataclass(frozen=True)
class VectorSearchResult:
    watermark_ts_ms: int
    hits: tuple[VectorHit, ...]

    def __iter__(self):
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)


@dataclass(frozen=True)
class SearchHealth:
    ready: bool
    index: str
    watermark_ts_ms: int
    generation: int | None
    error_code: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ready": self.ready, "index": self.index, "watermark_ts_ms": self.watermark_ts_ms,
        }
        if self.generation is not None:
            payload["generation"] = self.generation
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class _MetadataRow:
    item_id: str
    ts_ms: int
    platform: str
    channel: str
    version: str


@dataclass(frozen=True)
class _ShardState:
    metadata: ShardMetadata
    vectors: np.ndarray
    rows: tuple[_MetadataRow, ...]


@dataclass(frozen=True)
class _SearchState:
    identity: tuple[str, str, int, int, str]
    config: VectorIndexConfig
    manifest: ShardManifest
    shards: tuple[_ShardState, ...]


def _rank_scores(scores: np.ndarray, item_ids: np.ndarray, limit: int) -> np.ndarray:
    """Return score positions, resolving every equal-score boundary by item ID."""
    if limit <= 0 or len(scores) == 0:
        return np.empty(0, dtype=np.intp)
    count = min(limit, len(scores))
    if count == len(scores):
        return np.lexsort((item_ids, -scores)).astype(np.intp, copy=False)
    top = np.argpartition(-scores, count - 1)[:count]
    threshold = scores[top].min()
    strict = np.flatnonzero(scores > threshold)
    remaining = count - len(strict)
    equal = np.flatnonzero(scores == threshold)
    equal = equal[np.argsort(item_ids[equal], kind="stable")[:remaining]]
    candidates = np.concatenate((strict, equal))
    return candidates[np.lexsort((item_ids[candidates], -scores[candidates]))].astype(np.intp, copy=False)


class VectorSearcher:
    """Serve one immutable active generation, safely retaining the prior state."""

    def __init__(self, config: VectorIndexConfig, *, encoder: Any | None = None) -> None:
        self.config = config
        self._encoder = encoder
        self._lock = threading.RLock()
        self._state: _SearchState | None = None
        self._error_code = "index_unavailable"
        self.reload_if_changed()

    @staticmethod
    def _manifest_identity(config: VectorIndexConfig) -> tuple[str, str, int, int, str]:
        path = Path(config.data_dir) / "manifest.json"
        content = path.read_bytes()
        stat = path.stat()
        # The digest protects against an mtime/size collision while avoiding a
        # repeated scan of every (potentially 500 MiB) vector shard.
        return (
            str(Path(config.data_dir).resolve()), config.model_version, stat.st_mtime_ns,
            stat.st_size, hashlib.sha256(content).hexdigest(),
        )

    def _load_state(self, active_config: VectorIndexConfig, identity: tuple[str, str, int, int, str]) -> _SearchState:
        store = ShardStore.open(active_config)
        manifest = store.load_manifest()
        repository = VectorRepository(active_config)
        try:
            repository.init_schema()
            rows = repository.connection.execute(
                """SELECT er.shard_id, er.row_offset, f.feedback_id, f.ts_ms, f.platform,
                          f.channel, f.appversion
                   FROM embedding_record er JOIN feedback f ON f.feedback_id = er.feedback_id
                   WHERE er.model_version = ? AND er.shard_id IN (%s)"""
                % ",".join("?" for _ in manifest.shards),
                (active_config.model_version, *(shard.shard_id for shard in manifest.shards)),
            ).fetchall() if manifest.shards else []
        finally:
            repository.close()
        mapped: dict[str, dict[int, _MetadataRow]] = {shard.shard_id: {} for shard in manifest.shards}
        for row in rows:
            shard_id, offset = str(row[0]), int(row[1])
            if shard_id not in mapped or offset < 0 or offset in mapped[shard_id]:
                raise ValueError("invalid active-generation SQLite mappings")
            mapped[shard_id][offset] = _MetadataRow(
                item_id=str(row[2]), ts_ms=int(row[3]), platform=str(row[4] or ""),
                channel=str(row[5] or ""), version=str(row[6] or ""),
            )
        shards: list[_ShardState] = []
        for shard in manifest.shards:
            offsets = mapped[shard.shard_id]
            if set(offsets) != set(range(shard.row_count)):
                raise ValueError("active-generation SQLite mappings do not cover every vector row")
            vectors = np.load(store.shard_path(shard), mmap_mode="r", allow_pickle=False)
            vectors.flags.writeable = False
            shards.append(_ShardState(shard, vectors, tuple(offsets[index] for index in range(shard.row_count))))
        return _SearchState(identity, active_config, manifest, tuple(shards))

    def reload_if_changed(self) -> bool:
        """Validate and atomically install a newer manifest or promoted generation.

        A corrupt pointer/manifest is deliberately only recorded; requests with
        a previously healthy state continue to use that state.
        """
        try:
            active = active_index_config(self.config)
            identity = self._manifest_identity(active)
            with self._lock:
                if self._state is not None and self._state.identity == identity:
                    return False
            candidate = self._load_state(active, identity)
        except Exception as error:
            with self._lock:
                self._error_code = type(error).__name__
            return False
        with self._lock:
            if self._state is not None and self._state.identity == candidate.identity:
                return False
            self._state = candidate
            self._error_code = ""
        return True

    def _state_or_raise(self) -> _SearchState:
        self.reload_if_changed()
        with self._lock:
            if self._state is None:
                raise RuntimeError("vector index unavailable")
            return self._state

    def current_manifest(self) -> ShardManifest:
        return self._state_or_raise().manifest

    def health(self) -> SearchHealth:
        self.reload_if_changed()
        with self._lock:
            state = self._state
            if state is None:
                return SearchHealth(False, self.config.index_name, 0, None, self._error_code or "index_unavailable")
            return SearchHealth(True, self.config.index_name, state.manifest.watermark_ts_ms, state.manifest.generation)

    @staticmethod
    def _validate_queries(queries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        if isinstance(queries, (str, bytes)) or not isinstance(queries, Sequence):
            raise ValueError("queries must be an array")
        validated: list[dict[str, str]] = []
        seen: set[str] = set()
        for position, query in enumerate(queries):
            if not isinstance(query, Mapping):
                raise ValueError(f"query {position} must be an object")
            query_id, text, kind = query.get("id"), query.get("text"), query.get("kind")
            if not isinstance(query_id, str) or not query_id.strip():
                raise ValueError("query id must be non-empty")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("query text must be non-empty")
            if kind not in {"positive", "negative"}:
                raise ValueError("query kind must be positive or negative")
            if query_id in seen:
                raise ValueError("query ids must be unique")
            seen.add(query_id)
            validated.append({"id": query_id, "text": text, "kind": kind})
        return validated

    @staticmethod
    def _string_filter(filters: Mapping[str, Any], name: str) -> frozenset[str] | None:
        if name not in filters:
            return None
        value = filters[name]
        if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
            raise ValueError(f"{name} must be an array of strings")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{name} must be an array of strings")
        return frozenset(value)

    @classmethod
    def _validate_filters(cls, filters: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(filters, Mapping):
            raise ValueError("filters must be an object")
        unknown = set(filters) - _FILTER_KEYS
        if unknown:
            raise ValueError("unknown vector filters")
        if filters.get("unit") != "feedback":
            raise ValueError("unit must be feedback")
        result: dict[str, Any] = {"unit": "feedback"}
        for name in ("start_ts_ms", "end_ts_ms"):
            if name in filters:
                value = filters[name]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{name} must be a non-negative integer")
                result[name] = value
        if "start_ts_ms" in result and "end_ts_ms" in result and result["start_ts_ms"] >= result["end_ts_ms"]:
            raise ValueError("start_ts_ms must be before end_ts_ms")
        for name in ("platforms", "channels", "versions"):
            result[name] = cls._string_filter(filters, name)
        products = cls._string_filter(filters, "products")
        if products is not None and products - {_SUPPORTED_PRODUCT}:
            raise ValueError("unsupported product filter")
        result["products"] = products
        return result

    @staticmethod
    def _eligible(row: _MetadataRow, filters: Mapping[str, Any]) -> bool:
        if "start_ts_ms" in filters and row.ts_ms < filters["start_ts_ms"]:
            return False
        if "end_ts_ms" in filters and row.ts_ms >= filters["end_ts_ms"]:
            return False
        for key, field in (("platforms", row.platform), ("channels", row.channel), ("versions", row.version)):
            values = filters[key]
            if values is not None and field not in values:
                return False
        return True

    def _query_vectors(self, state: _SearchState, texts: Iterable[str]) -> np.ndarray:
        encoder = self._encoder
        if encoder is None:
            # Importing the concrete encoder is cheap; its torch/transformers
            # runtime stays lazy until the first actual query.
            from .encoder import QwenEmbeddingEncoder
            encoder = QwenEmbeddingEncoder(state.config)
            self._encoder = encoder
        vectors = np.asarray(encoder.encode_queries(list(texts)), dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[1] != state.manifest.dimension:
            raise ValueError("query encoder returned the wrong vector dimension")
        if not np.isfinite(vectors).all():
            raise ValueError("query encoder returned non-finite vectors")
        norms = np.linalg.norm(vectors.astype(np.float64, copy=False), axis=1)
        if not np.allclose(norms, 1.0, rtol=1e-4, atol=1e-4):
            raise ValueError("query vectors must be normalized")
        return vectors

    def search(self, queries: Sequence[Mapping[str, Any]], filters: Mapping[str, Any], limit: int) -> VectorSearchResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        validated_queries = self._validate_queries(queries)
        validated_filters = self._validate_filters(filters)
        state = self._state_or_raise()
        if not validated_queries:
            return VectorSearchResult(state.manifest.watermark_ts_ms, ())
        vectors = self._query_vectors(state, (query["text"] for query in validated_queries))
        if vectors.shape[0] != len(validated_queries):
            raise ValueError("query encoder returned the wrong vector count")
        hits: list[VectorHit] = []
        for query, vector in zip(validated_queries, vectors):
            candidates: list[tuple[float, str]] = []
            for shard in state.shards:
                eligible = np.fromiter((self._eligible(row, validated_filters) for row in shard.rows), dtype=bool, count=len(shard.rows))
                if not eligible.any():
                    continue
                offsets = np.flatnonzero(eligible)
                scores = np.einsum("d,nd->n", vector, shard.vectors[offsets], dtype=np.float32)
                item_ids = np.asarray([shard.rows[int(offset)].item_id for offset in offsets])
                for position in _rank_scores(scores, item_ids, limit):
                    candidates.append((float(scores[position]), str(item_ids[position])))
            candidates.sort(key=lambda item: (-item[0], item[1]))
            for rank, (score, item_id) in enumerate(candidates[:limit], start=1):
                if not math.isfinite(score):
                    raise ValueError("non-finite exact score")
                hits.append(VectorHit(item_id, query["id"], score, rank))
        return VectorSearchResult(state.manifest.watermark_ts_ms, tuple(hits))
