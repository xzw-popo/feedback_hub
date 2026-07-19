"""Strict HTTP adapter for the externally managed topic-vector service."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import requests

from .config import TopicMiningConfig


class VectorResponseError(ValueError):
    """Raised when a vector service response cannot be safely audited."""


@dataclass(frozen=True)
class VectorCapabilities:
    index: str
    schema_version: int
    supported_units: tuple[str, ...]
    watermark_ts_ms: int


@dataclass(frozen=True)
class VectorHit:
    item_id: str
    query_id: str
    score: float
    rank: int


@dataclass(frozen=True)
class VectorSearchResult:
    index: str
    watermark_ts_ms: int
    hits: tuple[VectorHit, ...]


class HttpVectorSearchClient:
    """Client for the versioned vector-search service contract.

    The caller retains responsibility for enforcing the hard source scope.  The
    service is a recall aid, so its identifiers are never treated as authority.
    """

    def __init__(
        self,
        config: TopicMiningConfig,
        *,
        request_fn: Callable[..., Any] | None = None,
    ) -> None:
        if not config.vector_api_url.strip():
            raise ValueError("TOPIC_VECTOR_API_URL must be configured")
        self.config = config
        self._session = requests.Session()
        self._request = request_fn or self._session.request
        self._base_url = config.vector_api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        if self.config.vector_api_token:
            return {"Authorization": f"Bearer {self.config.vector_api_token}"}
        return {}

    def _raise(self, message: str) -> None:
        redacted = message.replace(self.config.vector_api_token, "[REDACTED]") if self.config.vector_api_token else message
        # Do not preserve an upstream exception as __cause__/__context__: it may
        # contain credentials even after the human-readable message is redacted.
        raise VectorResponseError(redacted) from None

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Mapping[str, Any]:
        try:
            response = self._request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers(),
                timeout=30,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:  # requests errors and malformed provider responses
            self._raise(f"vector service {method} {path} failed: {error}")
        if not isinstance(payload, Mapping):
            self._raise(f"vector service {method} {path} returned a non-object response")
        return payload

    def _require_index(self, payload: Mapping[str, Any]) -> None:
        if payload.get("index") != self.config.vector_index:
            self._raise("vector service returned a response for the wrong index")

    @staticmethod
    def _watermark(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise VectorResponseError("vector service response has an invalid watermark_ts_ms")
        return value

    def capabilities(self) -> VectorCapabilities:
        payload = self._request_json("GET", "/capabilities", params={"index": self.config.vector_index})
        self._require_index(payload)
        if payload.get("schema_version") != 1:
            self._raise("vector service returned an unknown schema version")
        units = payload.get("supported_units")
        if (
            not isinstance(units, list)
            or not units
            or any(not isinstance(unit, str) or not unit for unit in units)
            or set(units) - {"feedback", "conversation"}
            or len(set(units)) != len(units)
        ):
            self._raise("vector service response has invalid supported_units")
        try:
            watermark = self._watermark(payload.get("watermark_ts_ms"))
        except VectorResponseError as error:
            self._raise(str(error))
        return VectorCapabilities(
            index=self.config.vector_index,
            schema_version=1,
            supported_units=tuple(units),
            watermark_ts_ms=watermark,
        )

    def search(
        self,
        queries: Sequence[Mapping[str, Any]],
        filters: Mapping[str, Any],
        limit: int,
    ) -> VectorSearchResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("vector search limit must be a positive integer")
        submitted_queries = self._validate_queries(queries)
        submitted_query_ids = {query["id"] for query in submitted_queries}
        payload = self._request_json(
            "POST",
            "/search",
            json={
                "index": self.config.vector_index,
                "queries": submitted_queries,
                "filters": dict(filters),
                "limit": limit,
            },
        )
        self._require_index(payload)
        try:
            watermark = self._watermark(payload.get("watermark_ts_ms"))
        except VectorResponseError as error:
            self._raise(str(error))
        raw_hits = payload.get("hits")
        if not isinstance(raw_hits, list):
            self._raise("vector service response has invalid hits")
        hits: list[VectorHit] = []
        seen: set[tuple[str, str]] = set()
        for position, raw_hit in enumerate(raw_hits):
            if not isinstance(raw_hit, Mapping):
                self._raise(f"vector service hit {position} is not an object")
            item_id, query_id = raw_hit.get("item_id"), raw_hit.get("query_id")
            score, rank = raw_hit.get("score"), raw_hit.get("rank")
            if not isinstance(item_id, str) or not item_id.strip() or not isinstance(query_id, str) or not query_id.strip():
                self._raise(f"vector service hit {position} has missing IDs")
            if query_id not in submitted_query_ids:
                self._raise(f"vector service hit {position} references an unsubmitted query_id")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                self._raise(f"vector service hit {position} has a non-finite score")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
                self._raise(f"vector service hit {position} has a non-positive rank")
            key = (query_id, item_id)
            if key in seen:
                self._raise("vector service response has duplicate (query_id, item_id) pairs")
            seen.add(key)
            hits.append(VectorHit(item_id=item_id, query_id=query_id, score=float(score), rank=rank))
        return VectorSearchResult(self.config.vector_index, watermark, tuple(hits))

    @staticmethod
    def _validate_queries(queries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        validated: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for position, query in enumerate(queries):
            if not isinstance(query, Mapping):
                raise ValueError(f"vector query {position} must be an object")
            query_id, text, kind = query.get("id"), query.get("text"), query.get("kind")
            if not isinstance(query_id, str) or not query_id.strip():
                raise ValueError(f"vector query {position} must have a non-empty id")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"vector query {position} must have non-empty text")
            if kind not in {"positive", "negative"}:
                raise ValueError(f"vector query {position} kind must be positive or negative")
            if query_id in seen_ids:
                raise ValueError(f"vector queries have duplicate id: {query_id}")
            seen_ids.add(query_id)
            validated.append(dict(query))
        return validated
