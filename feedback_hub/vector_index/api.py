"""Loopback-only HTTP schema v1 for the feedback vector searcher."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .config import VectorIndexConfig
from .search import VectorSearcher, VectorServiceUnavailable


class QueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: StrictStr
    text: StrictStr
    kind: Literal["positive", "negative"]


class SearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    index: StrictStr
    queries: list[QueryPayload]
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: Annotated[StrictInt, Field(gt=0)]


def create_app(config: VectorIndexConfig, encoder: Any | None = None) -> FastAPI:
    """Create the small localhost API without importing an embedding runtime."""
    app = FastAPI()
    searcher = VectorSearcher(config, encoder=encoder)
    app.state.vector_searcher = searcher

    @app.exception_handler(ValueError)
    async def invalid_request(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid vector search request"})

    @app.exception_handler(VectorServiceUnavailable)
    async def unavailable(_: Request, error: VectorServiceUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error) or "vector_service_unavailable"})

    @app.get("/capabilities")
    def capabilities(index: str) -> dict[str, object]:
        if index != config.index_name:
            raise HTTPException(status_code=404, detail="unknown vector index")
        manifest = searcher.ensure_ready()
        return {
            "index": config.index_name, "schema_version": 1, "supported_units": ["feedback"],
            "watermark_ts_ms": manifest.watermark_ts_ms,
        }

    @app.post("/search")
    def search(payload: SearchPayload) -> dict[str, object]:
        if payload.index != config.index_name:
            raise HTTPException(status_code=404, detail="unknown vector index")
        queries = [query.model_dump() for query in payload.queries]
        # Validation is deliberately before readiness: clients must receive a
        # deterministic 422 for malformed filters even during model/reload 503.
        searcher.validate_request(queries, payload.filters, payload.limit)
        searcher.ensure_ready()
        result = searcher.search(queries, payload.filters, payload.limit)
        return {
            "index": config.index_name, "watermark_ts_ms": result.watermark_ts_ms,
            "hits": [hit.to_dict() for hit in result.hits],
        }

    @app.get("/health")
    def health() -> dict[str, object]:
        try:
            searcher.ensure_ready()
        except VectorServiceUnavailable:
            pass
        state = searcher.health()
        if not state.ready:
            raise HTTPException(status_code=503, detail=state.error_code)
        return state.to_dict()

    return app


app = create_app(VectorIndexConfig())
