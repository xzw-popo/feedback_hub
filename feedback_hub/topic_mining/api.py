"""Internal HTTP entry points for audited topic-mining runs."""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
from datetime import timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .config import TopicMiningConfig
from .contracts import validate_topic_spec
from .export import export_topic_run
from .run_store import TopicRunStore
from .service import (
    RunVerificationError, default_store, get_topic_run, run_topic_job,
    submit_review_overrides, verify_topic_run,
)


_RUN_CONTEXTS: dict[str, tuple[TopicRunStore, TopicMiningConfig]] = {}


def start_run_async(run_id: str, *, store: TopicRunStore | None = None, config: TopicMiningConfig | None = None) -> None:
    if store is None or config is None:
        stored = _RUN_CONTEXTS.get(run_id)
        if stored is not None:
            store, config = stored
    thread = threading.Thread(target=run_topic_job, kwargs={"run_id": run_id, "store": store, "config": config}, daemon=True)
    thread.start()


def make_router(*, config: TopicMiningConfig | None = None, store: TopicRunStore | None = None) -> APIRouter:
    config = config or TopicMiningConfig()
    store = store or default_store(config)
    router = APIRouter(prefix="/api/topic-mining", tags=["topic-mining"])

    def require_token(request: Request) -> None:
        configured = config.api_token
        if not configured:
            return
        authorization = request.headers.get("Authorization", "")
        expected = f"Bearer {configured}"
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="topic mining authorization required")

    @router.get("/capabilities", dependencies=[Depends(require_token)])
    def capabilities() -> dict[str, Any]:
        return {
            "schema_versions": [1], "preferred_formats": ["xlsx", "jsonl"],
            "run_statuses": ["pending", "running", "review_ready", "verified", "failed", "paused_quota_exhausted"],
            "read_only_source": True, "formal_label_writeback": False,
        }

    @router.post("/runs", dependencies=[Depends(require_token)])
    def create_run(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            spec = validate_topic_spec(payload)
            watermark = _source_watermark(config.source_db_path, spec)
            run = store.create_or_get(spec, watermark)
            result = _public_run(run)
            if run["created"]:
                # Only the transaction that inserted a pending run starts work.
                # Keep the one positional call shape patchable in unit tests.
                _RUN_CONTEXTS[run["run_id"]] = (store, config)
                start_run_async(run["run_id"])
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_redact(str(exc), config)) from None

    @router.get("/runs/{run_id}", dependencies=[Depends(require_token)])
    def get_run(run_id: str) -> dict[str, Any]:
        run = get_topic_run(run_id, store=store)
        if run is None:
            raise HTTPException(status_code=404, detail="topic run not found")
        return _public_run(run)

    @router.get("/runs/{run_id}/review-queue", dependencies=[Depends(require_token)])
    def review_queue(run_id: str) -> dict[str, Any]:
        run = _get_or_404(run_id, store)
        path = Path(run["artifact_dir"]) / "review_queue.jsonl"
        return {"run_id": run_id, "items": _read_jsonl(path)}

    @router.post("/runs/{run_id}/overrides", dependencies=[Depends(require_token)])
    def overrides(run_id: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
        _get_or_404(run_id, store)
        try:
            return {"run_id": run_id, "overrides": submit_review_overrides(run_id, payload, store=store)}
        except (ValueError, RunVerificationError) as exc:
            raise HTTPException(status_code=400, detail=_redact(str(exc), config)) from None

    @router.post("/runs/{run_id}/verify", dependencies=[Depends(require_token)])
    def verify(run_id: str) -> dict[str, Any]:
        _get_or_404(run_id, store)
        try:
            return verify_topic_run(run_id, store=store)
        except RunVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_redact(str(exc), config)) from None

    @router.post("/runs/{run_id}/export", dependencies=[Depends(require_token)])
    def export(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _get_or_404(run_id, store)
        export_format = payload.get("format", "xlsx")
        try:
            path = export_topic_run(run_id, export_format, store=store)
        except RunVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_redact(str(exc), config)) from None
        return {"run_id": run_id, "artifact_name": path.name, "format": export_format}

    @router.get("/runs/{run_id}/artifacts/{artifact_name}", dependencies=[Depends(require_token)])
    def artifact(run_id: str, artifact_name: str):
        run = _get_or_404(run_id, store)
        # Resolve exclusively through the persisted manifest. Never normalize
        # or join a caller-controlled path before this allowlist lookup.
        manifest = _manifest(run)
        allowed = manifest.get("artifacts", {}) if isinstance(manifest, dict) else {}
        if artifact_name not in allowed or "/" in artifact_name or "\\" in artifact_name:
            raise HTTPException(status_code=404, detail="topic artifact not found")
        path = Path(run["artifact_dir"]) / artifact_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="topic artifact not found")
        return FileResponse(path, filename=artifact_name)

    return router


# Application default; tests needing isolated config use make_router directly.
router = make_router()


def _source_watermark(source_path: Path, spec: Any) -> int:
    """Read only enough metadata to key a run; never create or alter source DB."""
    path = Path(source_path)
    if path.is_file():
        try:
            with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
                row = connection.execute("SELECT MAX(ts_ms) FROM feedback").fetchone()
            if row and isinstance(row[0], int):
                return row[0]
        except sqlite3.Error:
            # The worker will record a stable failure with full auditing.  Use
            # deterministic scope cutoff only so create remains idempotent.
            pass
    return int(spec.scope.end_time.astimezone(timezone.utc).timestamp() * 1000)


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"], "status": run["status"], "stage": run["stage"],
        "error_code": run.get("error_code"), "error_message": run.get("error_message"),
        "created": bool(run.get("created", False)), "source_watermark_ms": run["source_watermark_ms"],
    }


def _get_or_404(run_id: str, store: TopicRunStore) -> dict[str, Any]:
    run = get_topic_run(run_id, store=store)
    if run is None:
        raise HTTPException(status_code=404, detail="topic run not found")
    return run


def _manifest(run: dict[str, Any]) -> dict[str, Any]:
    import json
    try:
        value = json.loads(run.get("manifest_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    import json
    if not path.is_file():
        return []
    return [value for line in path.read_text(encoding="utf-8").splitlines() if line.strip() for value in [json.loads(line)] if isinstance(value, dict)]


def _redact(message: str, config: TopicMiningConfig) -> str:
    for secret in (config.api_token, config.vector_api_token, os.environ.get("LLM_API_KEY", "")):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]
