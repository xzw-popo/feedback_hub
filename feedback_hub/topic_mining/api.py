"""Internal HTTP entry points for audited topic-mining runs."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .config import TopicMiningConfig
from .contracts import validate_topic_spec
from .run_store import TopicRunStore, WorkerClaimLostError
from .service import (
    RunVerificationError, _artifact_valid, _effective_data_cutoff, _verify_manifest, read_verified_artifact_bytes, default_store, get_topic_run, run_topic_job,
    submit_review_overrides, verify_topic_run,
)


_FINAL_DELIVERABLES = frozenset({
    "final_results.jsonl",
    "quality_report.json",
    "feedback_list.xlsx",
})


@dataclass(frozen=True)
class WorkerStart:
    scheduled: bool
    reason: str


def start_run_async(
    run_id: str,
    *,
    store: TopicRunStore | None = None,
    config: TopicMiningConfig | None = None,
) -> WorkerStart:
    """Persistently claim a run before launching its background worker."""
    config = config or TopicMiningConfig()
    store = store or default_store(config)
    claim = store.claim_worker(
        run_id, lease_seconds=config.worker_lease_seconds,
    )
    if not claim.claimed or not claim.claim_token:
        return WorkerStart(False, claim.reason)
    try:
        thread = threading.Thread(
            target=_run_claimed_job,
            kwargs={
                "run_id": run_id,
                "store": store,
                "config": config,
                "claim_token": claim.claim_token,
            },
            daemon=True,
        )
        thread.start()
    except Exception:
        # This process knows no worker started, so persist a diagnosable,
        # immediately recoverable failure. Process crashes retain their lease
        # and follow the backend stale-worker policy instead.
        store.fail_worker_claim(
            run_id,
            claim.claim_token,
            error_code="worker_start_failed",
            error_message="topic worker could not start",
        )
        return WorkerStart(False, "worker_start_failed")
    return WorkerStart(True, "scheduled")


def _run_claimed_job(
    *,
    run_id: str,
    store: TopicRunStore,
    config: TopicMiningConfig,
    claim_token: str,
) -> None:
    stop_heartbeat = threading.Event()
    claim_lost = threading.Event()
    heartbeat_failed = threading.Event()
    heartbeat_interval = max(0.25, config.worker_lease_seconds / 3)
    heartbeat_thread: threading.Thread | None = None
    heartbeat_started = False

    def heartbeat() -> None:
        while not stop_heartbeat.wait(heartbeat_interval):
            try:
                renewed = store.renew_worker_claim(
                    run_id,
                    claim_token,
                    lease_seconds=config.worker_lease_seconds,
                )
            except Exception:
                heartbeat_failed.set()
                claim_lost.set()
                return
            if not renewed:
                claim_lost.set()
                return

    try:
        try:
            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()
            heartbeat_started = True
        except Exception:
            store.fail_worker_claim(
                run_id,
                claim_token,
                error_code="worker_heartbeat_start_failed",
                error_message="topic worker heartbeat could not start",
            )
            return
        claimed_store = store.for_worker_claim(
            claim_token, cancellation_event=claim_lost,
        )
        try:
            run_topic_job(run_id, store=claimed_store, config=config)
            if claim_lost.is_set():
                raise WorkerClaimLostError("worker_claim_lost")
        except WorkerClaimLostError:
            if heartbeat_failed.is_set():
                store.fail_worker_claim(
                    run_id,
                    claim_token,
                    error_code="worker_heartbeat_lost",
                    error_message="topic worker heartbeat was lost",
                )
        except Exception:
            store.fail_worker_claim(
                run_id,
                claim_token,
                error_code="topic_run_error",
                error_message="topic worker failed",
            )
    finally:
        stop_heartbeat.set()
        try:
            if heartbeat_started and heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)
        finally:
            store.release_worker_claim(run_id, claim_token)


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
            scheduled = False
            if run["created"]:
                start = start_run_async(run["run_id"], store=store, config=config)
                scheduled = start.scheduled
            current = store.get(run["run_id"]) or run
            current["created"] = run["created"]
            result = _public_run(current)
            result["scheduled"] = scheduled
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_redact(str(exc), config)) from None

    @router.get("/runs/{run_id}", dependencies=[Depends(require_token)])
    def get_run(run_id: str) -> dict[str, Any]:
        run = get_topic_run(run_id, store=store)
        if run is None:
            raise HTTPException(status_code=404, detail="topic run not found")
        return _public_run(run)

    @router.post("/runs/{run_id}/resume", dependencies=[Depends(require_token)])
    def resume_run(run_id: str) -> dict[str, Any]:
        _get_or_404(run_id, store)
        start = start_run_async(run_id, store=store, config=config)
        if not start.scheduled:
            status_code = 503 if start.reason == "worker_start_failed" else 409
            raise HTTPException(status_code=status_code, detail=start.reason)
        run = _get_or_404(run_id, store)
        result = _public_run(run)
        result["scheduled"] = True
        return result

    @router.get("/runs/{run_id}/review-queue", dependencies=[Depends(require_token)])
    def review_queue(run_id: str) -> dict[str, Any]:
        run = _get_or_404(run_id, store)
        path = Path(run["artifact_dir"]) / "review_queue.jsonl"
        try:
            manifest = _manifest(run); _verify_manifest(manifest, Path(run["artifact_dir"]))
            raw = read_verified_artifact_bytes(manifest, path)
            return {"run_id": run_id, "items": _parse_jsonl_bytes(raw)}
        except (ValueError, RunVerificationError) as exc:
            raise HTTPException(status_code=409, detail=_redact(str(exc), config)) from None

    @router.post("/runs/{run_id}/overrides", dependencies=[Depends(require_token)])
    def overrides(run_id: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
        _get_or_404(run_id, store)
        try:
            return {"run_id": run_id, "overrides": submit_review_overrides(run_id, payload, store=store)}
        except (ValueError, RunVerificationError) as exc:
            raise HTTPException(status_code=409 if isinstance(exc, RunVerificationError) else 422, detail=_redact(str(exc), config)) from None

    @router.post("/runs/{run_id}/verify", dependencies=[Depends(require_token)])
    def verify(run_id: str) -> dict[str, Any]:
        _get_or_404(run_id, store)
        try:
            return verify_topic_run(run_id, store=store)
        except RunVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_redact(str(exc), config)) from None

    @router.post("/runs/{run_id}/export", dependencies=[Depends(require_token)])
    def export(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _get_or_404(run_id, store)
        export_format = payload.get("format", "xlsx")
        try:
            from .export import export_topic_run

            path = export_topic_run(run_id, export_format, store=store)
        except RunVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_redact(str(exc), config)) from None
        return {"run_id": run_id, "artifact_name": path.name, "format": export_format}

    @router.get("/runs/{run_id}/artifacts/{artifact_name}", dependencies=[Depends(require_token)])
    def artifact(run_id: str, artifact_name: str):
        run = _get_or_404(run_id, store)
        # Download authority comes from run state and this final-deliverable
        # contract, never from arbitrary names present in an internal manifest.
        if (
            run["status"] != "verified"
            or artifact_name not in _FINAL_DELIVERABLES
            or "/" in artifact_name
            or "\\" in artifact_name
        ):
            raise HTTPException(status_code=404, detail="topic artifact not found")
        manifest = _manifest(run)
        artifacts = manifest.get("artifacts", {}) if isinstance(manifest, dict) else {}
        if not isinstance(artifacts, dict) or artifact_name not in artifacts:
            raise HTTPException(status_code=404, detail="topic artifact not found")
        try:
            _verify_manifest(manifest, Path(run["artifact_dir"]))
        except RunVerificationError:
            raise HTTPException(status_code=409, detail="topic artifact hash mismatch") from None
        path = Path(run["artifact_dir"]) / artifact_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="topic artifact not found")
        try:
            raw = read_verified_artifact_bytes(manifest, path)
        except RunVerificationError:
            raise HTTPException(status_code=409, detail="topic artifact hash mismatch") from None
        return Response(content=raw, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{artifact_name}"'})

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
    manifest = _manifest(run)
    classifier = manifest.get("classifier", {}) if isinstance(manifest.get("classifier"), dict) else {}
    source_watermark_ms = _effective_data_cutoff(run, manifest)
    return {
        "run_id": run["run_id"], "status": run["status"], "stage": run["stage"],
        "error_code": run.get("error_code"), "error_message": run.get("error_message"),
        "created": bool(run.get("created", False)), "source_watermark_ms": source_watermark_ms,
        "quality": {
            "funnel": manifest.get("funnel", {}), "source_watermark_ms": source_watermark_ms,
            "vector_watermark_ms": manifest.get("vector_watermark_ms"),
            "unresolved": {key: manifest.get(key, 0) for key in ("unresolved_classifier_items", "unresolved_parser_items", "duplicate_item_ids", "missing_link_items", "unresolved_vector_items", "unresolved_coverage_items")},
            "models": classifier.get("models", []), "retry_total": classifier.get("retry_total", 0),
        },
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
    try:
        values = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("invalid review queue artifact")
            values.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid review queue artifact") from exc
    if any(not isinstance(value, dict) for value in values):
        raise ValueError("invalid review queue artifact")
    return values


def _parse_jsonl_bytes(raw: bytes) -> list[dict[str, Any]]:
    try:
        return _read_jsonl_bytes(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid review queue artifact") from exc


def _read_jsonl_bytes(raw: bytes) -> list[dict[str, Any]]:
    values = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid review queue artifact")
        values.append(value)
    return values


def _redact(message: str, config: TopicMiningConfig) -> str:
    for secret in (config.api_token, config.vector_api_token, os.environ.get("LLM_API_KEY", "")):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]
