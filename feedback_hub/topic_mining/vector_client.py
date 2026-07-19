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

    def _raise(self, message: str, error: Exception | None = None) -> None:
        redacted = message.replace(self.config.vector_api_token, "[REDACTED]") if self.config.vector_api_token else message
        if error is None:
            raise VectorResponseError(redacted)
        raise VectorResponseError(redacted) from error

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
            self._raise(f"vector service {method} {path} failed: {error}", error)
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
        if not isinstance(units, list) or not units or any(not isinstance(unit, str) or not unit for unit in units):
            self._raise("vector service response has invalid supported_units")
        try:
            watermark = self._watermark(payload.get("watermark_ts_ms"))
        except VectorResponseError as error:
            self._raise(str(error), error)
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
        payload = self._request_json(
            "POST",
            "/search",
            json={
                "index": self.config.vector_index,
                "queries": [dict(query) for query in queries],
                "filters": dict(filters),
                "limit": limit,
            },
        )
        self._require_index(payload)
        try:
            watermark = self._watermark(payload.get("watermark_ts_ms"))
        except VectorResponseError as error:
            self._raise(str(error), error)
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
