"""Loopback-only HTTP schema v1 for the feedback vector searcher."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import VectorIndexConfig
from .search import VectorSearcher


class SearchPayload(BaseModel):
    index: str
    queries: list[dict[str, Any]]
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(gt=0)


def create_app(config: VectorIndexConfig, encoder: Any | None = None) -> FastAPI:
    """Create the small localhost API without importing an embedding runtime."""
    app = FastAPI()
    searcher = VectorSearcher(config, encoder=encoder)
    app.state.vector_searcher = searcher

    @app.exception_handler(ValueError)
    @app.exception_handler(RuntimeError)
    async def invalid_request(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid vector search request"})

    @app.get("/capabilities")
    def capabilities(index: str) -> dict[str, object]:
        if index != config.index_name:
            raise HTTPException(status_code=404, detail="unknown vector index")
        manifest = searcher.current_manifest()
        return {
            "index": config.index_name, "schema_version": 1, "supported_units": ["feedback"],
            "watermark_ts_ms": manifest.watermark_ts_ms,
        }

    @app.post("/search")
    def search(payload: SearchPayload) -> dict[str, object]:
        if payload.index != config.index_name:
            raise HTTPException(status_code=404, detail="unknown vector index")
        result = searcher.search(payload.queries, payload.filters, payload.limit)
        return {
            "index": config.index_name, "watermark_ts_ms": result.watermark_ts_ms,
            "hits": [hit.to_dict() for hit in result.hits],
        }

    @app.get("/health")
    def health() -> dict[str, object]:
        state = searcher.health()
        if not state.ready:
            raise HTTPException(status_code=503, detail=state.error_code)
        return state.to_dict()

    return app


app = create_app(VectorIndexConfig())
