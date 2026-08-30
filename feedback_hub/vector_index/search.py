"""Memory-mapped exact cosine search for the active feedback generation."""
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


_FILTER_KEYS = frozenset({"unit", "start_ts_ms", "end_ts_ms", "platforms", "channels", "products", "versions"})
_SUPPORTED_PRODUCT = "微信输入法"


class VectorServiceUnavailable(RuntimeError):
    """A stable non-sensitive signal for an unhealthy active search service."""


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
    stale: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"ready": self.ready, "index": self.index, "watermark_ts_ms": self.watermark_ts_ms}
        if self.generation is not None:
            payload["generation"] = self.generation
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.stale:
            payload["stale"] = True
        return payload


@dataclass(frozen=True)
class SearchMetrics:
    eligibility_passes: int = 0
    score_chunks: int = 0
    max_scored_rows: int = 0


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
    """Return positions with deterministic item-ID resolution at Top-K ties."""
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
    """Serve a validated immutable generation and retain old state on failure."""

    def __init__(self, config: VectorIndexConfig, *, encoder: Any | None = None, score_chunk_rows: int = 8192) -> None:
        if isinstance(score_chunk_rows, bool) or not isinstance(score_chunk_rows, int) or score_chunk_rows <= 0:
            raise ValueError("score_chunk_rows must be a positive integer")
        self.config = config
        self._encoder = encoder
        self._score_chunk_rows = score_chunk_rows
        self._lock = threading.RLock()
        self._encoder_lock = threading.Lock()
        self._state: _SearchState | None = None
        self._reload_error = "index_unavailable"
        self._model_error = ""
        self._last_metrics = SearchMetrics()
        self.reload_if_changed()

    @staticmethod
    def _manifest_identity(config: VectorIndexConfig) -> tuple[str, str, int, int, str]:
        path = Path(config.data_dir) / "manifest.json"
        content = path.read_bytes()
        stat = path.stat()
        return (str(Path(config.data_dir).resolve()), config.model_version, stat.st_mtime_ns, stat.st_size, hashlib.sha256(content).hexdigest())

    @staticmethod
    def _error_name(error: Exception) -> str:
        # Type names are safe and stable enough to reveal operational class,
        # unlike exception text which may contain paths or provider details.
        return type(error).__name__

    def _load_state(self, active_config: VectorIndexConfig, identity: tuple[str, str, int, int, str]) -> _SearchState:
        store = ShardStore.open(active_config)
        manifest = store.load_manifest()
        repository = VectorRepository(active_config)
        try:
            repository.init_schema()
            if not repository.publication_matches(
                publication_key=str(store.root), model_version=active_config.model_version,
                generation=manifest.generation, watermark_ts_ms=manifest.watermark_ts_ms,
            ):
                raise ValueError("active generation publication marker is missing or stale")
            persisted = repository.connection.execute(
                """SELECT shard_id, model_version, path, dimension, row_count, checksum
                   FROM embedding_shard WHERE state = 'active' AND model_version = ?""",
                (active_config.model_version,),
            ).fetchall()
            by_id = {str(row[0]): row for row in persisted}
            if set(by_id) != {shard.shard_id for shard in manifest.shards}:
                raise ValueError("active generation shard rows do not match manifest")
            for shard in manifest.shards:
                row = by_id[shard.shard_id]
                if (str(row[1]), str(Path(row[2]).resolve()), int(row[3]), int(row[4]), str(row[5])) != (
                    shard.model_version, str(store.shard_path(shard)), shard.dimension, shard.row_count, shard.checksum,
                ):
                    raise ValueError("active generation shard metadata does not match manifest")
            rows = repository.connection.execute(
                """SELECT er.shard_id, er.row_offset, er.content_hash, f.feedback_id, f.ts_ms,
                          f.platform, f.channel, f.appversion, f.text
                   FROM embedding_record er JOIN feedback f ON f.feedback_id = er.feedback_id
                   WHERE er.model_version = ? AND er.shard_id IN (%s)"""
                % ",".join("?" for _ in manifest.shards),
                (active_config.model_version, *(shard.shard_id for shard in manifest.shards)),
            ).fetchall() if manifest.shards else []
        finally:
            repository.close()
        mapped: dict[str, dict[int, _MetadataRow]] = {shard.shard_id: {} for shard in manifest.shards}
        item_ids: set[str] = set()
        for row in rows:
            shard_id, offset, content_hash, item_id = str(row[0]), int(row[1]), str(row[2]), str(row[3])
            if shard_id not in mapped or offset < 0 or offset in mapped[shard_id] or item_id in item_ids:
                raise ValueError("invalid active-generation SQLite mappings")
            if hashlib.sha256(str(row[8]).encode("utf-8")).hexdigest() != content_hash:
                raise ValueError("active-generation feedback content hash mismatch")
            item_ids.add(item_id)
            mapped[shard_id][offset] = _MetadataRow(item_id, int(row[4]), str(row[5] or ""), str(row[6] or ""), str(row[7] or ""))
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
        try:
            active = active_index_config(self.config)
            identity = self._manifest_identity(active)
            with self._lock:
                if self._state is not None and self._state.identity == identity:
                    self._reload_error = ""
                    return False
            candidate = self._load_state(active, identity)
        except Exception as error:
            with self._lock:
                self._reload_error = self._error_name(error)
            return False
        with self._lock:
            if self._state is not None and self._state.identity == candidate.identity:
                return False
            self._state = candidate
            self._reload_error = ""
        return True

    def _state_or_raise(self) -> _SearchState:
        self.reload_if_changed()
        with self._lock:
            if self._state is None:
                raise VectorServiceUnavailable(self._reload_error or self._model_error or "index_unavailable")
            return self._state

    def _current_health(self) -> SearchHealth:
        with self._lock:
            state, reload_error, model_error = self._state, self._reload_error, self._model_error
        error = reload_error or model_error
        if state is None:
            return SearchHealth(False, self.config.index_name, 0, None, error or "index_unavailable")
        if error:
            return SearchHealth(False, self.config.index_name, state.manifest.watermark_ts_ms, state.manifest.generation, error, stale=True)
        return SearchHealth(True, self.config.index_name, state.manifest.watermark_ts_ms, state.manifest.generation)

    def current_manifest(self) -> ShardManifest:
        return self._state_or_raise().manifest

    def health(self) -> SearchHealth:
        self.reload_if_changed()
        return self._current_health()

    def ensure_ready(self) -> ShardManifest:
        """Refresh state and model readiness for API endpoints that must be 503-safe."""
        state = self._state_or_raise()
        try:
            self._ensure_encoder_ready(state)
        except Exception as error:
            with self._lock:
                self._model_error = self._error_name(error)
        else:
            with self._lock:
                self._model_error = ""
        health = self._current_health()
        if not health.ready:
            raise VectorServiceUnavailable(health.error_code)
        return state.manifest

    def last_search_metrics(self) -> SearchMetrics:
        with self._lock:
            return self._last_metrics

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
        if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{name} must be an array of strings")
        return frozenset(value)

    @classmethod
    def _validate_filters(cls, filters: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(filters, Mapping):
            raise ValueError("filters must be an object")
        if set(filters) - _FILTER_KEYS:
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
        return (("start_ts_ms" not in filters or row.ts_ms >= filters["start_ts_ms"])
                and ("end_ts_ms" not in filters or row.ts_ms < filters["end_ts_ms"])
                and all(filters[key] is None or field in filters[key]
                        for key, field in (("platforms", row.platform), ("channels", row.channel), ("versions", row.version))))

    def _ensure_encoder_ready(self, state: _SearchState) -> Any:
        """Serialize first model construction for concurrent health/search calls."""
        with self._encoder_lock:
            if self._encoder is None:
                from .encoder import QwenEmbeddingEncoder
                self._encoder = QwenEmbeddingEncoder(state.config)
            ready = getattr(self._encoder, "ensure_ready", None)
            if callable(ready):
                ready()
            return self._encoder

    def _query_vectors(self, state: _SearchState, texts: Iterable[str]) -> np.ndarray:
        encoder = self._ensure_encoder_ready(state)
        try:
            vectors = np.asarray(encoder.encode_queries(list(texts)), dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[1] != state.manifest.dimension or not np.isfinite(vectors).all():
                raise ValueError("query encoder returned invalid vectors")
            squared_norms = np.einsum("nd,nd->n", vectors, vectors, dtype=np.float32)
            if not np.allclose(squared_norms, 1.0, rtol=2e-4, atol=2e-4):
                raise ValueError("query vectors must be normalized")
            return vectors
        except Exception as error:
            with self._lock:
                self._model_error = self._error_name(error)
            raise VectorServiceUnavailable(self._model_error) from None

    def validate_request(
        self, queries: Sequence[Mapping[str, Any]], filters: Mapping[str, Any], limit: int,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        """Pure request validation for API precedence before readiness checks."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        return self._validate_queries(queries), self._validate_filters(filters)

    def _eligible_shards(self, state: _SearchState, filters: Mapping[str, Any]) -> tuple[list[np.ndarray], SearchMetrics]:
        masks = [np.fromiter((self._eligible(row, filters) for row in shard.rows), dtype=bool, count=len(shard.rows)) for shard in state.shards]
        return masks, SearchMetrics(eligibility_passes=len(state.shards))

    def _top_for_shard(self, vector: np.ndarray, shard: _ShardState, eligible: np.ndarray, limit: int) -> tuple[list[tuple[float, str]], int, int]:
        selected: list[tuple[float, str]] = []
        chunks = max_rows = 0
        for start in range(0, len(shard.rows), self._score_chunk_rows):
            stop = min(start + self._score_chunk_rows, len(shard.rows))
            mask = eligible[start:stop]
            if not mask.any():
                continue
            scores = np.einsum("d,nd->n", vector, shard.vectors[start:stop], dtype=np.float32)
            positions = np.flatnonzero(mask)
            item_ids = np.asarray([shard.rows[start + int(position)].item_id for position in positions])
            local_scores = scores[positions]
            for position in _rank_scores(local_scores, item_ids, limit):
                selected.append((float(local_scores[position]), str(item_ids[position])))
            selected.sort(key=lambda item: (-item[0], item[1]))
            del selected[limit:]
            chunks += 1
            max_rows = max(max_rows, stop - start)
        return selected, chunks, max_rows

    def search(self, queries: Sequence[Mapping[str, Any]], filters: Mapping[str, Any], limit: int) -> VectorSearchResult:
        validated_queries, validated_filters = self.validate_request(queries, filters, limit)
        state = self._state_or_raise()
        if not validated_queries:
            return VectorSearchResult(state.manifest.watermark_ts_ms, ())
        vectors = self._query_vectors(state, (query["text"] for query in validated_queries))
        if vectors.shape[0] != len(validated_queries):
            raise ValueError("query encoder returned the wrong vector count")
        eligibility, metrics = self._eligible_shards(state, validated_filters)
        hits: list[VectorHit] = []
        for query, vector in zip(validated_queries, vectors):
            candidates: list[tuple[float, str]] = []
            for shard, mask in zip(state.shards, eligibility):
                top, chunks, max_rows = self._top_for_shard(vector, shard, mask, limit)
                candidates.extend(top)
                metrics = SearchMetrics(metrics.eligibility_passes, metrics.score_chunks + chunks, max(metrics.max_scored_rows, max_rows))
            candidates.sort(key=lambda item: (-item[0], item[1]))
            for rank, (score, item_id) in enumerate(candidates[:limit], start=1):
                if not math.isfinite(score):
                    raise ValueError("non-finite exact score")
                hits.append(VectorHit(item_id, query["id"], score, rank))
        with self._lock:
            self._last_metrics = metrics
        return VectorSearchResult(state.manifest.watermark_ts_ms, tuple(hits))
