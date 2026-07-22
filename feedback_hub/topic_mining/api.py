"""Internal HTTP entry points for audited topic-mining runs."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .budgets import candidate_budget, review_sample_budget
from .config import TopicMiningConfig
from .contracts import load_persisted_topic_spec, topic_spec_hash, validate_topic_spec
from .run_store import TopicRunStore, WorkerClaimLostError
from .source import create_source_snapshot, source_freshness, validate_source_coverage
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
    heartbeat_failure_persisted = threading.Event()
    retry_wait = threading.Event()
    heartbeat_interval = max(0.25, config.worker_lease_seconds / 3)
    heartbeat_thread: threading.Thread | None = None
    heartbeat_started = False

    def persist_heartbeat_failure(
        *, cancellation_event: threading.Event | None,
    ) -> bool:
        while not heartbeat_failure_persisted.is_set():
            try:
                persisted = store.fail_worker_claim(
                    run_id,
                    claim_token,
                    error_code="worker_heartbeat_lost",
                    error_message="topic worker heartbeat was lost",
                )
            except Exception:
                waiter = cancellation_event or retry_wait
                if waiter.wait(0.1):
                    return False
                continue
            if persisted:
                heartbeat_failure_persisted.set()
            # False means the token was already replaced or the run already
            # reached another terminal state; the stale worker must stop.
            return persisted
        return True

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
                persist_heartbeat_failure(cancellation_event=stop_heartbeat)
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
                persist_heartbeat_failure(cancellation_event=None)
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
        freshness = source_freshness(
            config.source_db_path,
            sync_interval_seconds=config.source_sync_interval_seconds,
        )
        freshness["available_from"] = (
            datetime.fromtimestamp(
                freshness["available_from_ms"] / 1000, tz=timezone.utc,
            ).isoformat()
            if freshness["available_from_ms"] is not None else None
        )
        freshness["available_through"] = (
            datetime.fromtimestamp(
                freshness["available_through_ms"] / 1000, tz=timezone.utc,
            ).isoformat()
            if freshness["available_through_ms"] is not None else None
        )
        return {
            "schema_versions": [1], "preferred_formats": ["xlsx", "jsonl"],
            "run_statuses": ["pending", "running", "review_ready", "verified", "failed", "paused_quota_exhausted"],
            "read_only_source": True, "formal_label_writeback": False,
            "supported_units": ["feedback"],
            "default_time_days": 14,
            "run_modes": {
                "standard": {
                    "candidate_limit": config.standard_candidate_max,
                    "result_limit": None,
                    "candidate_budget": {
                        "minimum": config.standard_candidate_min,
                        "per_day": config.standard_candidates_per_day,
                        "maximum": config.standard_candidate_max,
                    },
                    "review_sample_budget": {
                        "per_day": config.review_samples_per_day,
                        "maximum": config.review_sample_max,
                    },
                },
                "exhaustive": {
                    "candidate_limit": config.exhaustive_candidate_limit,
                    "result_limit": None,
                },
            },
            "authentication": "internal_network_boundary",
            "source_freshness": freshness,
        }

    @router.post("/runs", dependencies=[Depends(require_token)])
    def create_run(payload: dict[str, Any]) -> dict[str, Any]:
        incoming_snapshot: Path | None = None
        try:
            spec = validate_topic_spec(payload)
            incoming_dir = config.data_dir / ".incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            incoming_snapshot = incoming_dir / (
                hashlib.sha256(os.urandom(32)).hexdigest() + ".sqlite"
            )
            snapshot = create_source_snapshot(
                config.source_db_path, incoming_snapshot,
            )
            validate_source_coverage(snapshot.path, spec)
            if snapshot.coverage_watermark_ms is None:
                raise ValueError("source coverage metadata is unavailable")
            watermark = int(snapshot.coverage_watermark_ms)
            spec_hash = topic_spec_hash(spec)
            run_id = hashlib.sha256(
                f"{spec_hash}:{watermark}".encode("utf-8")
            ).hexdigest()[:16]
            final_snapshot = store.artifacts_dir / run_id / "source_snapshot.sqlite"
            snapshot_data = asdict(snapshot)
            snapshot_data["path"] = str(final_snapshot)
            manifest = {
                "manifest_version": 2,
                "stage": "snapshot",
                "artifacts": {"source_snapshot.sqlite": snapshot.sha256},
                "stages": {
                    "snapshot": {
                        "inputs": {},
                        "outputs": {"source_snapshot.sqlite": snapshot.sha256},
                        "input_count": 0,
                        "output_count": snapshot.row_count,
                    },
                },
                "funnel": {"snapshot_count": snapshot.row_count},
                "source_snapshot": snapshot_data,
                "source_watermark_ms": watermark,
                "source_sha256": snapshot.sha256,
                "candidate_budget": candidate_budget(spec, config),
                "review_budget": review_sample_budget(spec, config),
            }
            run = store.create_or_get(
                spec,
                watermark,
                initial_files={"source_snapshot.sqlite": incoming_snapshot},
                initial_manifest=manifest,
                initial_stage="snapshot",
            )
            scheduled = False
            if run["created"]:
                start = start_run_async(run["run_id"], store=store, config=config)
                scheduled = start.scheduled
            current = store.get(run["run_id"]) or run
            current["created"] = run["created"]
            result = _public_run(current)
            result["scheduled"] = scheduled
            return result
        except (sqlite3.Error, OSError):
            raise HTTPException(
                status_code=503, detail="source_snapshot_unavailable",
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_redact(str(exc), config)) from None
        finally:
            if incoming_snapshot is not None:
                _cleanup_incoming_snapshot(incoming_snapshot)

    @router.get("/runs/{run_id}", dependencies=[Depends(require_token)])
    def get_run(run_id: str) -> dict[str, Any]:
        run = get_topic_run(run_id, store=store)
        if run is None:
            raise HTTPException(status_code=404, detail="topic run not found")
        try:
            return _public_run(run)
        except RunVerificationError as exc:
            raise HTTPException(
                status_code=409, detail=_redact(str(exc), config),
            ) from None

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
    def review_queue(
        run_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=50),
    ) -> dict[str, Any]:
        run = _get_or_404(run_id, store)
        path = Path(run["artifact_dir"]) / "review_queue.jsonl"
        try:
            manifest = _manifest(run); _verify_manifest(manifest, Path(run["artifact_dir"]))
            raw = read_verified_artifact_bytes(manifest, path)
            items = _parse_jsonl_bytes(raw)
            next_offset = offset + limit if offset + limit < len(items) else None
            return {
                "run_id": run_id,
                "items": items[offset:offset + limit],
                "total": len(items),
                "next_offset": next_offset,
            }
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
        manifest = _manifest(_get_or_404(run_id, store))
        return {
            "run_id": run_id, "artifact_name": path.name, "format": export_format,
            **{
                key: manifest.get(key)
                for key in (
                    "mode", "result_scope", "matched_total",
                    "returned_feedback", "possibly_more_matches",
                )
            },
        }

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


def _cleanup_incoming_snapshot(snapshot_path: Path) -> None:
    """Best-effort cleanup must never replace the request's stable result."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(snapshot_path) + suffix).unlink(missing_ok=True)
        except OSError:
            continue


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    manifest = _manifest(run)
    classifier = manifest.get("classifier", {}) if isinstance(manifest.get("classifier"), dict) else {}
    source_watermark_ms = _effective_data_cutoff(run, manifest)
    result_scope = _public_result_scope(run, manifest)
    return {
        "run_id": run["run_id"], "status": run["status"], "stage": run["stage"],
        "error_code": run.get("error_code"), "error_message": run.get("error_message"),
        "created": bool(run.get("created", False)), "source_watermark_ms": source_watermark_ms,
        **result_scope,
        "quality": {
            "funnel": manifest.get("funnel", {}), "source_watermark_ms": source_watermark_ms,
            "vector_watermark_ms": manifest.get("vector_watermark_ms"),
            "candidate_budget": manifest.get("candidate_budget"),
            "review_budget": manifest.get("review_budget"),
            "unresolved": {key: manifest.get(key, 0) for key in ("unresolved_classifier_items", "unresolved_parser_items", "duplicate_item_ids", "missing_link_items", "unresolved_vector_items", "unresolved_coverage_items")},
            "models": classifier.get("models", []), "retry_total": classifier.get("retry_total", 0),
        },
    }


_RESULT_SCOPE_KEYS = (
    "mode", "result_scope", "matched_total", "returned_feedback",
    "possibly_more_matches",
)


def _public_result_scope(
    run: dict[str, Any], manifest: dict[str, Any],
) -> dict[str, Any]:
    """Expose scope for legacy verified runs without mutating their record."""
    if all(key in manifest for key in _RESULT_SCOPE_KEYS):
        return {key: manifest[key] for key in _RESULT_SCOPE_KEYS}
    if run.get("status") != "verified":
        return {key: None for key in _RESULT_SCOPE_KEYS}
    verified = manifest.get("verified")
    matched_total = (
        verified.get("matched_count") if isinstance(verified, dict) else None
    )
    if isinstance(matched_total, bool) or not isinstance(matched_total, int):
        return {key: None for key in _RESULT_SCOPE_KEYS}
    try:
        spec = load_persisted_topic_spec(json.loads(run["spec_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {key: None for key in _RESULT_SCOPE_KEYS}
    classified_count = manifest.get("classified_count", matched_total)
    if isinstance(classified_count, bool) or not isinstance(classified_count, int):
        classified_count = matched_total
    retrieved_count = manifest.get("retrieved_candidate_count", classified_count)
    if isinstance(retrieved_count, bool) or not isinstance(retrieved_count, int):
        retrieved_count = classified_count
    returned_feedback = min(matched_total, 100) if spec.mode == "standard" else matched_total
    return {
        "mode": spec.mode,
        "result_scope": (
            "representative"
            if spec.mode == "standard" and matched_total > returned_feedback
            else "reviewed"
        ),
        "matched_total": matched_total,
        "returned_feedback": returned_feedback,
        "possibly_more_matches": (
            retrieved_count > classified_count
            or (
                spec.mode == "standard"
                and matched_total > returned_feedback
            )
        ),
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
