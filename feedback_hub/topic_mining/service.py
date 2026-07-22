"""Audited orchestration and verification for isolated topic-mining runs.

The source database is only ever handed to :mod:`source`, whose connection
helper opens it with SQLite's ``mode=ro`` URI.  Everything produced here lives
under the run artifact directory, so a failed or paused run is inspectable and
can resume without changing feedback records or formal labels.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from urllib.parse import urlparse
from dataclasses import asdict
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .classifier import ClassificationResult, classify_candidates
from .config import TopicMiningConfig
from .diversity import select_diverse_candidates, select_representative_results
from .contracts import TopicSpec, topic_spec_hash, validate_topic_spec
from .retrieval import RecallHit, build_semantic_queries, build_vector_filters, hybrid_recall, recall_budget
from .review import apply_review_overrides, build_review_queue, persist_review_artifacts
from .run_store import RunPublicationConflictError, TopicRunStore
from .source import DataCoverageError, SourceReadinessError, build_item_contexts, create_source_snapshot, fetch_scoped_items
from .vector_client import HttpVectorSearchClient


class RunVerificationError(ValueError):
    """Raised when an audited run cannot safely become exportable."""


_STAGES = ("snapshot", "hard_scope", "hybrid_recall", "classify", "review_queue", "review_ready")
_MANIFEST_VERSION = 2
_UNRESOLVED_KEYS = (
    "unresolved_classifier_items", "unresolved_parser_items", "duplicate_item_ids",
    "missing_link_items", "unresolved_vector_items", "unresolved_coverage_items",
)
_CLASSIFICATION_WORK_FILES = (
    "classification_batches.jsonl",
    "classification_batches.jsonl.checkpoint.jsonl",
    "classification_batches.jsonl.failures.jsonl",
    "classification_batches.jsonl.run_state.json",
    "classification_audit.jsonl",
    "classified.jsonl",
)
_STANDARD_RESULT_LIMIT = 100


def default_store(config: TopicMiningConfig | None = None) -> TopicRunStore:
    config = config or TopicMiningConfig()
    return TopicRunStore(config.data_dir / "runs.db", config.data_dir / "runs")


def get_topic_run(run_id: str, *, store: TopicRunStore | None = None) -> dict[str, Any] | None:
    store = store or default_store()
    return store.get(run_id)


def run_topic_job(
    run_id: str,
    *,
    store: TopicRunStore | None = None,
    config: TopicMiningConfig | None = None,
    vector_client: Any | None = None,
    classifier_routes: Sequence[Any] | None = None,
    classifier_call_fn: Any | None = None,
    model_routes: Sequence[Any] | None = None,
    model_call_fn: Any | None = None,
) -> dict[str, Any]:
    """Run (or resume) a topic job. External clients are injectable for E2E tests.

    A status update is persisted after every valid artifact.  The callback
    parameters deliberately make no lower-level retrieval/model tuning public.
    ``model_*`` is the public integration-contract spelling; the older
    ``classifier_*`` names remain supported for existing internal callers.
    """
    if classifier_routes is not None and model_routes is not None:
        raise ValueError("provide only one of classifier_routes or model_routes")
    if classifier_call_fn is not None and model_call_fn is not None:
        raise ValueError("provide only one of classifier_call_fn or model_call_fn")
    selected_routes = model_routes if model_routes is not None else classifier_routes
    selected_call_fn = model_call_fn if model_call_fn is not None else classifier_call_fn
    config = config or TopicMiningConfig()
    store = store or default_store(config)
    run = _require_run(run_id, store)
    if run["status"] == "verified":
        verify_topic_run(run_id, store=store)
        return _require_run(run_id, store)
    try:
        _run_pipeline(
            run_id=run_id, store=store, config=config, vector_client=vector_client,
            classifier_routes=selected_routes, classifier_call_fn=selected_call_fn,
        )
    except Exception as exc:  # stable status is more useful than a background traceback
        _record_failure(run_id, store, exc, config)
    return _require_run(run_id, store)


def _run_pipeline(
    *,
    run_id: str,
    store: TopicRunStore,
    config: TopicMiningConfig,
    vector_client: Any | None = None,
    classifier_routes: Sequence[Any] | None = None,
    classifier_call_fn: Any | None = None,
) -> None:
    run = _require_run(run_id, store)
    spec = validate_topic_spec(json.loads(run["spec_json"]))
    artifact_dir = Path(run["artifact_dir"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(run, artifact_dir)
    manifest.setdefault("manifest_version", _MANIFEST_VERSION)
    manifest.setdefault("stages", {})
    _set_status(run_id, store, "running", stage=run.get("stage") or "created")

    snapshot_path = artifact_dir / "source_snapshot.sqlite"
    if not _stage_valid(manifest, "snapshot", artifact_dir):
        _ensure_worker_active(run_id, store)
        snapshot_workspace = _stage_workspace(store, artifact_dir, "snapshot")
        direct_snapshot_stage = snapshot_workspace == artifact_dir
        staged_snapshot_path = snapshot_workspace / (
            snapshot_path.name + ".staged"
            if direct_snapshot_stage
            else snapshot_path.name
        )
        if direct_snapshot_stage:
            staged_snapshot_path.unlink(missing_ok=True)
        persisted_watermark_ms = int(run["source_watermark_ms"])
        snapshot = create_source_snapshot(
            config.source_db_path,
            staged_snapshot_path,
        )
        if snapshot.coverage_watermark_ms != persisted_watermark_ms:
            raise RunVerificationError("source_watermark_mismatch")
        persisted_snapshot_meta = manifest.get("source_snapshot")
        trusted_snapshot_digest = manifest.get("source_sha256")
        if (
            trusted_snapshot_digest is None
            and isinstance(persisted_snapshot_meta, Mapping)
        ):
            trusted_snapshot_digest = persisted_snapshot_meta.get("sha256")
        if trusted_snapshot_digest is not None and (
            not isinstance(trusted_snapshot_digest, str)
            or len(trusted_snapshot_digest) != 64
            or snapshot.sha256 != trusted_snapshot_digest
        ):
            if direct_snapshot_stage:
                staged_snapshot_path.unlink(missing_ok=True)
            raise RunVerificationError("source_snapshot_recovery_mismatch")
        _ensure_worker_active(run_id, store)
        if direct_snapshot_stage:
            os.replace(staged_snapshot_path, snapshot_path)
            snapshot_hashes = {snapshot_path.name: snapshot.sha256}
        else:
            snapshot_hashes = _promote_stage_files(
                run_id, store, artifact_dir, snapshot_workspace,
                [snapshot_path.name],
            )
        snapshot_data = asdict(snapshot)
        snapshot_data["path"] = str(snapshot_path)
        manifest.update({
            "source_snapshot": snapshot_data,
            "source_watermark_ms": snapshot.coverage_watermark_ms,
            "source_sha256": snapshot.sha256,
        })
        _checkpoint(
            run_id, store, artifact_dir, manifest, "snapshot", [snapshot_path],
            output_hashes=snapshot_hashes,
        )
    snapshot_meta = manifest.get("source_snapshot") or {}
    source_watermark_ms = int(run["source_watermark_ms"])

    scoped_path = artifact_dir / "scoped_items.jsonl"
    contexts_path = artifact_dir / "item_contexts.json"
    if not _stage_valid(manifest, "hard_scope", artifact_dir):
        _ensure_worker_active(run_id, store)
        items = fetch_scoped_items(snapshot_path, spec)
        contexts = build_item_contexts(items, spec.unit, window_ms=1_800_000)
        _ensure_worker_active(run_id, store)
        scope_workspace = _stage_workspace(store, artifact_dir, "hard_scope")
        _write_jsonl(scope_workspace / scoped_path.name, items)
        _atomic_json(scope_workspace / contexts_path.name, contexts)
        scope_hashes = _promote_stage_files(
            run_id, store, artifact_dir, scope_workspace,
            [scoped_path.name, contexts_path.name],
        )
        manifest["hard_scope"] = {"item_count": len(items), "unit": spec.unit}
        # Clear only the failure owned by this stage after the repaired stage
        # has produced valid outputs. Other unresolved failures remain intact.
        manifest["unresolved_coverage_items"] = 0
        _checkpoint(
            run_id, store, artifact_dir, manifest, "hard_scope",
            [scoped_path, contexts_path], output_hashes=scope_hashes,
        )
    items = _read_jsonl(scoped_path)
    contexts = _read_json(contexts_path, {})

    recall_path = artifact_dir / "recall_candidates.jsonl"
    recall_manifest_path = artifact_dir / "recall_manifest.json"
    selected_path = artifact_dir / "selected_candidates.jsonl"
    if not _stage_valid(manifest, "hybrid_recall", artifact_dir):
        _ensure_worker_active(run_id, store)
        client = vector_client or HttpVectorSearchClient(config)
        capabilities = client.capabilities()
        if spec.unit not in capabilities.supported_units:
            raise ValueError("vector_index_unsupported_unit")
        channel_top_k, _recall_pool_limit = recall_budget(spec, config)
        result = client.search(build_semantic_queries(spec), build_vector_filters(spec), channel_top_k)
        _ensure_worker_active(run_id, store)
        recall_workspace = _stage_workspace(store, artifact_dir, "hybrid_recall")
        plan = hybrid_recall(
            items, spec, vector_hits=result.hits, config=config, artifact_dir=recall_workspace,
            source_watermark_ms=source_watermark_ms, vector_watermark_ms=result.watermark_ts_ms,
        )
        selected_plan = select_diverse_candidates(
            plan.candidates,
            limit=_classification_candidate_limit(spec, config),
        )
        _write_jsonl(
            recall_workspace / selected_path.name,
            [candidate.to_dict() for candidate in selected_plan],
        )
        _ensure_worker_active(run_id, store)
        recall_hashes = _promote_stage_files(
            run_id, store, artifact_dir, recall_workspace,
            [recall_path.name, recall_manifest_path.name, selected_path.name],
        )
        manifest.update({
            "vector_watermark_ms": result.watermark_ts_ms,
            "vector_index": result.index,
            "hybrid_recall": {
                "candidate_count": len(plan.candidates),
                "retrieved_candidate_count": plan.retrieved_candidate_count,
                "recall_pool_count": len(plan.candidates),
                "rejected_out_of_scope_ids": list(plan.rejected_out_of_scope_ids),
            },
            "retrieved_candidate_count": plan.retrieved_candidate_count,
            "recall_pool_count": len(plan.candidates),
            "selected_candidate_count": len(selected_plan),
            "retrieval_config": _safe_config(config),
            "unresolved_vector_items": 0,
        })
        manifest.setdefault("funnel", {}).update({
            "retrieved_candidate_count": plan.retrieved_candidate_count,
            "recall_pool_count": len(plan.candidates),
            "selected_candidate_count": len(selected_plan),
        })
        _checkpoint(
            run_id, store, artifact_dir, manifest, "hybrid_recall",
            [recall_path, recall_manifest_path, selected_path], output_hashes=recall_hashes,
        )
    recalls = [_recall_from_dict(row) for row in _read_jsonl(recall_path)]
    selected_recalls = _classification_candidates(manifest, artifact_dir, recalls)

    classified_path = artifact_dir / "classified.jsonl"
    audit_path = artifact_dir / "classification_audit.jsonl"
    classifications_rebuilt = False
    if not _stage_valid(manifest, "classify", artifact_dir):
        _ensure_worker_active(run_id, store)
        resume_classification = _classification_resume_safe(manifest, artifact_dir)
        classify_workspace = _stage_workspace(store, artifact_dir, "classify")
        if not resume_classification:
            _purge_classification_checkpoints(classify_workspace)
        _seed_classification_workspace(
            artifact_dir,
            classify_workspace,
            resume=resume_classification,
            preserve_audit=_classification_output_safe(
                manifest, artifact_dir, "classification_audit.jsonl",
            ),
            partial_audits=_authenticated_classification_partials(
                manifest, artifact_dir,
            ),
        )
        results, stats = classify_candidates(
            spec, selected_recalls, artifact_dir=classify_workspace, contexts=contexts, routes=classifier_routes,
            config=config, resume=resume_classification, call_fn=classifier_call_fn,
            cancel_check=lambda: _ensure_worker_active(run_id, store),
            progress_callback=lambda: _publish_classification_progress(
                run_id, store, artifact_dir, classify_workspace, manifest,
            ),
        )
        _ensure_worker_active(run_id, store)
        classification_hashes = _promote_stage_files(
            run_id, store, artifact_dir, classify_workspace,
            [
                name for name in _CLASSIFICATION_WORK_FILES
                if (classify_workspace / name).is_file()
            ],
        )
        manifest["classifier"] = _safe_json(stats)
        manifest["classifier"].update(_classification_quality(audit_path))
        manifest["funnel"] = {
            "hard_scope_count": len(items),
            "candidate_count": len(recalls),
            "retrieved_candidate_count": manifest.get("retrieved_candidate_count", len(recalls)),
            "recall_pool_count": len(recalls),
            "selected_candidate_count": len(selected_recalls),
            "classified_count": len(results),
        }
        manifest["recall_pool_count"] = len(recalls)
        manifest["selected_candidate_count"] = len(selected_recalls)
        manifest["classified_count"] = len(results)
        manifest["unresolved_classifier_items"] = max(0, len(selected_recalls) - len(results))
        manifest["unresolved_parser_items"] = int(stats.get("failed", 0))
        # Classification can safely pause; it must never publish review_ready.
        if stats.get("run_status") == "paused_quota_exhausted":
            _checkpoint_attempt(
                run_id, store, artifact_dir, manifest, "classify", [audit_path],
                output_hashes=classification_hashes,
            )
            _set_status(run_id, store, "paused_quota_exhausted", stage="classify")
            return
        if manifest["unresolved_classifier_items"] or manifest["unresolved_parser_items"]:
            _checkpoint_attempt(
                run_id, store, artifact_dir, manifest, "classify", [audit_path],
                output_hashes=classification_hashes,
            )
            raise ValueError("unresolved_classifier_items")
        _checkpoint(
            run_id, store, artifact_dir, manifest, "classify",
            [classified_path, audit_path], output_hashes=classification_hashes,
        )
        classifications_rebuilt = True
    classifications = [_classification_from_dict(row) for row in _read_jsonl(classified_path)]

    queue_path = artifact_dir / "review_queue.jsonl"
    overrides_path = artifact_dir / "review_overrides.jsonl"
    if classifications_rebuilt or not _stage_valid(manifest, "review_queue", artifact_dir):
        _ensure_worker_active(run_id, store)
        queue = build_review_queue(
            run_id, classifications, {row.item_id: row for row in recalls},
            contexts=contexts,
        )
        review_workspace = _stage_workspace(store, artifact_dir, "review_queue")
        persist_review_artifacts(
            review_workspace, queue,
            _read_jsonl(overrides_path) if overrides_path.exists() else [],
        )
        review_hashes = _promote_stage_files(
            run_id, store, artifact_dir, review_workspace,
            [queue_path.name, overrides_path.name],
        )
        manifest["review_queue"] = {"item_count": len(queue)}
        _checkpoint(
            run_id, store, artifact_dir, manifest, "review_queue",
            [queue_path, overrides_path], output_hashes=review_hashes,
        )

    # The final persisted stage says only that review material is ready. A
    # separately requested verify step is required before data leaves the run.
    manifest.setdefault("unresolved_classifier_items", 0)
    manifest.setdefault("unresolved_parser_items", 0)
    for key in _UNRESOLVED_KEYS:
        manifest.setdefault(key, 0)
    _ensure_worker_active(run_id, store)
    review_ready_path = artifact_dir / "review_ready.json"
    ready_workspace = _stage_workspace(store, artifact_dir, "review_ready")
    _atomic_json(ready_workspace / review_ready_path.name, {"classification_count": len(classifications), "queue_count": len(_read_jsonl(queue_path))})
    ready_hashes = _promote_stage_files(
        run_id, store, artifact_dir, ready_workspace,
        [review_ready_path.name],
    )
    _checkpoint(
        run_id, store, artifact_dir, manifest, "review_ready",
        [queue_path, overrides_path], output_hashes=ready_hashes,
    )
    _set_status(run_id, store, "review_ready", stage="review_ready")


def submit_review_overrides(
    run_id: str,
    overrides: Sequence[Mapping[str, Any]],
    *,
    store: TopicRunStore | None = None,
) -> list[dict[str, Any]]:
    store = store or default_store()
    run = _require_run(run_id, store)
    if run["status"] != "review_ready":
        raise RunVerificationError("run_not_review_ready")
    artifact_dir = Path(run["artifact_dir"])
    manifest = _load_manifest(run, artifact_dir)
    _verify_manifest(manifest, artifact_dir)
    _require_artifact(manifest, artifact_dir / "classified.jsonl")
    review_queue_path = artifact_dir / "review_queue.jsonl"
    _require_artifact(manifest, review_queue_path)
    _require_artifact(manifest, artifact_dir / "recall_candidates.jsonl")
    _require_artifact(manifest, artifact_dir / "item_contexts.json")
    classifications = [_classification_from_dict(row) for row in _read_required_jsonl(artifact_dir / "classified.jsonl", manifest)]
    recalls = [_recall_from_dict(row) for row in _read_required_jsonl(artifact_dir / "recall_candidates.jsonl", manifest)]
    selected_recalls = _classification_candidates(manifest, artifact_dir, recalls)
    _require_classification_coverage(classifications, selected_recalls)
    _require_frozen_candidate_counts(
        manifest, artifact_dir, recalls, selected_recalls, classifications,
    )
    contexts = _read_required_json(artifact_dir / "item_contexts.json", manifest)
    spec = validate_topic_spec(json.loads(run["spec_json"]))
    evidence_sources = _authoritative_evidence_sources(recalls, contexts)
    # Validate before touching the persistent file. Overrides may supply only
    # evidence copied from hash-verified source text or persisted contexts.
    apply_review_overrides(
        classifications, overrides,
        allowed_labels={entry["id"] for entry in spec.classification_labels},
        evidence_sources=evidence_sources,
    )
    _require_review_decisions(
        _read_required_jsonl(review_queue_path, manifest), overrides,
    )
    override_rows = [dict(row) for row in overrides]
    override_bytes = _jsonl_bytes(override_rows)
    proposed = json.loads(json.dumps(manifest))
    _invalidate_export_artifacts(proposed)
    pending_hashes = {
        "review_overrides.jsonl": hashlib.sha256(override_bytes).hexdigest(),
    }
    # Overrides are a declared output of review_queue. Re-checkpoint that
    # stage before review_ready so the full verification chain continues to
    # authenticate the latest approved reviewer decisions.
    _prepare_checkpoint_manifest(
        proposed, artifact_dir, "review_queue", pending_hashes,
    )
    _prepare_checkpoint_manifest(
        proposed, artifact_dir, "review_ready", pending_hashes,
    )
    _publish_terminal_mutation(
        run,
        store,
        proposed,
        stage="review_ready",
        status="review_ready",
        files={"review_overrides.jsonl": override_bytes},
        delete_names=(
            "final_reviewed.jsonl", "final_results.jsonl",
            "quality_report.json", "feedback_list.xlsx",
        ),
    )
    return override_rows


def verify_topic_run(run_id: str, *, store: TopicRunStore | None = None) -> dict[str, Any]:
    """Validate every membership claim and make an immutable verified result."""
    store = store or default_store()
    run = _require_run(run_id, store)
    if run["status"] not in {"review_ready", "verified"}:
        raise RunVerificationError("run_not_review_ready")
    artifact_dir = Path(run["artifact_dir"])
    manifest = _load_manifest(run, artifact_dir)
    _verify_manifest(manifest, artifact_dir)
    spec = validate_topic_spec(json.loads(run["spec_json"]))
    data_cutoff_ms = _effective_data_cutoff(
        run, manifest, require_snapshot=True,
    )
    _validate_run_identity(run, spec, data_cutoff_ms)
    for key in _UNRESOLVED_KEYS:
        if int(manifest.get(key, 0) or 0) > 0:
            raise RunVerificationError(key)
    _require_stage_chain(manifest, artifact_dir)
    if run["status"] == "verified":
        _require_artifact(manifest, artifact_dir / "final_reviewed.jsonl")
    recall_path, classified_path, contexts_path, overrides_path, review_queue_path = (artifact_dir / "recall_candidates.jsonl", artifact_dir / "classified.jsonl", artifact_dir / "item_contexts.json", artifact_dir / "review_overrides.jsonl", artifact_dir / "review_queue.jsonl")
    for path in (recall_path, classified_path, contexts_path, overrides_path, review_queue_path):
        _require_artifact(manifest, path)
    recalls = [_recall_from_dict(row) for row in _read_required_jsonl(recall_path, manifest)]
    classifications = [_classification_from_dict(row) for row in _read_required_jsonl(classified_path, manifest)]
    contexts = _read_required_json(contexts_path, manifest)
    selected_recalls = _classification_candidates(manifest, artifact_dir, recalls)
    _require_classification_coverage(classifications, selected_recalls)
    _require_frozen_candidate_counts(
        manifest, artifact_dir, recalls, selected_recalls, classifications,
    )
    allowed = {row["id"] for row in spec.classification_labels}
    if any(row.label not in allowed for row in classifications):
        raise RunVerificationError("invalid_label")
    by_id = {hit.item_id: hit.item for hit in recalls}
    for result in classifications:
        item = by_id[result.item_id]
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RunVerificationError("missing_text")
        if not _valid_source_url(item.get("source_url")):
            raise RunVerificationError("missing_link")
        context_texts = _context_texts(contexts, result.item_id)
        if result.label == "matched" and (not result.evidence or not all(any(evidence in source for source in [text, *context_texts]) for evidence in result.evidence)):
            raise RunVerificationError("invalid_evidence")
        if not _item_in_scope(item, spec):
            raise RunVerificationError("invalid_scope")
    overrides = _read_required_jsonl(overrides_path, manifest)
    evidence_sources = _authoritative_evidence_sources(recalls, contexts)
    try:
        merged = apply_review_overrides(
            classifications, overrides, allowed_labels=allowed,
            evidence_sources=evidence_sources,
        )
    except ValueError as exc:
        code = "invalid_evidence" if "evidence" in str(exc) else "invalid_review_override"
        raise RunVerificationError(code) from exc
    _require_review_decisions(
        _read_required_jsonl(review_queue_path, manifest), overrides,
    )
    final_rows = [
        _final_row(
            result, by_id[result.item_id], run_id,
            _context_texts(contexts, result.item_id), data_cutoff_ms,
        )
        for result in merged if result.label == "matched"
    ]
    _validate_final_rows(
        final_rows, spec, contexts=contexts, expected_run_id=run_id,
        expected_data_cutoff_ms=data_cutoff_ms,
    )
    scope = _result_scope(
        spec, final_rows, {hit.item_id: hit for hit in recalls}, manifest,
        classified_count=len(classifications),
    )
    final_bytes = _jsonl_bytes(final_rows)
    proposed = json.loads(json.dumps(manifest))
    proposed.update(scope)
    proposed["verified"] = {
        "matched_count": len(final_rows),
        "verified_at_ms": int(time.time() * 1000),
    }
    _prepare_checkpoint_manifest(
        proposed,
        artifact_dir,
        "verified",
        {"final_reviewed.jsonl": hashlib.sha256(final_bytes).hexdigest()},
    )
    _publish_terminal_mutation(
        run,
        store,
        proposed,
        stage="verified",
        status="verified",
        files={"final_reviewed.jsonl": final_bytes},
    )
    return {"run_id": run_id, "status": "verified", "matched_count": len(final_rows)}


def _result_scope(
    spec: TopicSpec,
    rows: Sequence[Mapping[str, Any]],
    recall_by_id: Mapping[str, RecallHit],
    manifest: Mapping[str, Any],
    *,
    classified_count: int | None = None,
) -> dict[str, Any]:
    """Describe exactly what a downloadable result may claim to cover."""
    matched_total = len(rows)
    if spec.mode == "standard":
        returned_feedback = len(select_representative_results(
            rows, recall_by_id, limit=_STANDARD_RESULT_LIMIT,
        ))
    else:
        returned_feedback = matched_total
    fallback_classified = (
        classified_count
        if classified_count is not None
        else (len(recall_by_id) if recall_by_id else matched_total)
    )
    manifest_classified = manifest.get("classified_count", fallback_classified)
    if isinstance(manifest_classified, bool) or not isinstance(manifest_classified, int):
        manifest_classified = fallback_classified
    retrieved = manifest.get("retrieved_candidate_count", len(recall_by_id) or manifest_classified)
    if isinstance(retrieved, bool) or not isinstance(retrieved, int):
        retrieved = len(recall_by_id) or manifest_classified
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
            retrieved > manifest_classified
            or (
                spec.mode == "standard"
                and matched_total > returned_feedback
            )
        ),
    }


def _final_row(
    result: ClassificationResult,
    item: Mapping[str, Any],
    run_id: str,
    context_texts: Sequence[str],
    data_cutoff_ms: int,
) -> dict[str, Any]:
    evidence_source = {value: ("source_text" if value in str(item.get("text", "")) else "context") for value in result.evidence}
    return {
        "item_id": result.item_id, "label": result.label, "confidence": result.confidence,
        "evidence": list(result.evidence), "reason": result.reason, "source": result.source,
        "source_item": dict(item), "context_texts": list(context_texts), "evidence_source": evidence_source,
        "run_id": run_id, "data_cutoff_ms": data_cutoff_ms,
    }


def _classification_candidate_limit(
    spec: TopicSpec, config: TopicMiningConfig,
) -> int:
    return (
        config.standard_candidate_limit
        if spec.mode == "standard"
        else config.exhaustive_candidate_limit
    )


def _classification_candidates(
    manifest: Mapping[str, Any],
    artifact_dir: Path,
    recalls: Sequence[RecallHit],
) -> list[RecallHit]:
    """Load the frozen classification boundary, or preserve legacy full recall."""
    selected_path = artifact_dir / "selected_candidates.jsonl"
    if not selected_path.is_file():
        return list(recalls)
    _require_artifact(manifest, selected_path)
    selected = [
        _recall_from_dict(row)
        for row in _read_required_jsonl(selected_path, manifest)
    ]
    recall_by_id = {hit.item_id: hit for hit in recalls}
    if len(recall_by_id) != len(recalls):
        raise RunVerificationError("duplicate_item_id")
    selected_ids = [hit.item_id for hit in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise RunVerificationError("duplicate_item_id")
    if not set(selected_ids) <= set(recall_by_id):
        raise RunVerificationError("selected_candidate_not_in_recall_pool")
    if any(hit != recall_by_id[hit.item_id] for hit in selected):
        raise RunVerificationError("selected_candidate_mismatch")
    _require_manifest_count(manifest, "recall_pool_count", len(recalls))
    _require_manifest_count(manifest, "selected_candidate_count", len(selected))
    funnel = manifest.get("funnel")
    if not isinstance(funnel, Mapping):
        raise RunVerificationError("selected_candidate_count_mismatch")
    _require_manifest_count(funnel, "recall_pool_count", len(recalls))
    _require_manifest_count(funnel, "selected_candidate_count", len(selected))
    return selected


def _require_manifest_count(
    manifest: Mapping[str, Any], key: str, expected: int,
) -> None:
    value = manifest.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise RunVerificationError(f"{key}_mismatch")


def _require_classification_coverage(
    classifications: Sequence[ClassificationResult],
    candidates: Sequence[RecallHit],
) -> None:
    expected = {hit.item_id for hit in candidates}
    actual = [row.item_id for row in classifications]
    if len(actual) != len(set(actual)):
        raise RunVerificationError("duplicate_item_id")
    if set(actual) != expected:
        raise RunVerificationError("classification_coverage")


def _require_frozen_candidate_counts(
    manifest: Mapping[str, Any],
    artifact_dir: Path,
    recalls: Sequence[RecallHit],
    selected: Sequence[RecallHit],
    classifications: Sequence[ClassificationResult],
) -> None:
    """Require every persisted budget count to agree for frozen selections."""
    if not (artifact_dir / "selected_candidates.jsonl").is_file():
        return
    for values in (manifest, manifest.get("funnel")):
        if not isinstance(values, Mapping):
            raise RunVerificationError("classified_count_mismatch")
        _require_manifest_count(values, "recall_pool_count", len(recalls))
        _require_manifest_count(values, "selected_candidate_count", len(selected))
        _require_manifest_count(values, "classified_count", len(classifications))
        retrieved = values.get("retrieved_candidate_count")
        if (
            isinstance(retrieved, bool)
            or not isinstance(retrieved, int)
            or retrieved < len(recalls)
        ):
            raise RunVerificationError("retrieved_candidate_count_mismatch")


def _validate_final_rows(
    rows: Sequence[Mapping[str, Any]],
    spec: TopicSpec,
    *,
    contexts: Mapping[str, Any] | None = None,
    expected_run_id: str,
    expected_data_cutoff_ms: int,
) -> None:
    expected_data_cutoff_ms = _required_data_cutoff(expected_data_cutoff_ms)
    seen: set[str] = set()
    allowed = {entry["id"] for entry in spec.classification_labels}
    for row in rows:
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise RunVerificationError("duplicate_item_id")
        seen.add(item_id)
        if row.get("run_id") != expected_run_id:
            raise RunVerificationError("invalid_run_id")
        data_cutoff_ms = row.get("data_cutoff_ms")
        if isinstance(data_cutoff_ms, bool) or not isinstance(data_cutoff_ms, int) or data_cutoff_ms != expected_data_cutoff_ms:
            raise RunVerificationError("invalid_data_cutoff")
        if row.get("label") != "matched" or row["label"] not in allowed:
            raise RunVerificationError("invalid_label")
        item = row.get("source_item")
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise RunVerificationError("missing_text")
        item_ts_ms = item.get("ts_ms")
        if isinstance(item_ts_ms, bool) or not isinstance(item_ts_ms, int) or item_ts_ms > expected_data_cutoff_ms:
            raise RunVerificationError("invalid_data_cutoff")
        url = item.get("source_url")
        if not _valid_source_url(url):
            raise RunVerificationError("missing_link")
        evidence = row.get("evidence")
        context_texts = list(row.get("context_texts", [])) or _context_texts(contexts or {}, str(item_id))
        if not isinstance(evidence, list) or not evidence or not all(isinstance(value, str) and any(value in source for source in [item["text"], *context_texts]) for value in evidence):
            raise RunVerificationError("invalid_evidence")
        if not _item_in_scope(item, spec):
            raise RunVerificationError("invalid_scope")


def _required_data_cutoff(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunVerificationError("invalid_data_cutoff")
    return value


def _effective_data_cutoff(
    run: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    require_snapshot: bool = False,
) -> int:
    cutoff = _required_data_cutoff(run.get("source_watermark_ms"))
    if require_snapshot and "source_watermark_ms" not in manifest:
        raise RunVerificationError("source_snapshot_required")
    if "source_watermark_ms" in manifest:
        manifest_cutoff = _required_data_cutoff(
            manifest.get("source_watermark_ms"),
        )
        if manifest_cutoff != cutoff:
            raise RunVerificationError("source_watermark_mismatch")

    snapshot_meta = manifest.get("source_snapshot")
    if require_snapshot and snapshot_meta is None:
        raise RunVerificationError("source_snapshot_required")
    if snapshot_meta is not None:
        if (
            not isinstance(snapshot_meta, Mapping)
            or "max_ts_ms" not in snapshot_meta
            or "coverage_watermark_ms" not in snapshot_meta
        ):
            raise RunVerificationError("source_watermark_mismatch")
        snapshot_watermark = _required_data_cutoff(
            snapshot_meta.get("coverage_watermark_ms"),
        )
        if snapshot_watermark != cutoff:
            raise RunVerificationError("source_watermark_mismatch")
        snapshot_max_ts_ms = snapshot_meta.get("max_ts_ms")
        if (
            snapshot_max_ts_ms is not None
            and (
                isinstance(snapshot_max_ts_ms, bool)
                or not isinstance(snapshot_max_ts_ms, int)
            )
        ):
            raise RunVerificationError("source_watermark_mismatch")

    snapshot_path = Path(str(run.get("artifact_dir", ""))) / "source_snapshot.sqlite"
    if require_snapshot and not snapshot_path.is_file():
        raise RunVerificationError("source_snapshot_required")
    if require_snapshot:
        artifacts = manifest.get("artifacts")
        snapshot_digest = (
            artifacts.get(snapshot_path.name)
            if isinstance(artifacts, Mapping)
            else None
        )
        metadata_digest = (
            snapshot_meta.get("sha256")
            if isinstance(snapshot_meta, Mapping)
            else None
        )
        if (
            not isinstance(snapshot_digest, str)
            or metadata_digest != snapshot_digest
            or manifest.get("source_sha256") != snapshot_digest
        ):
            raise RunVerificationError("source_snapshot_required")
    if snapshot_path.is_file():
        try:
            with sqlite3.connect(
                f"file:{snapshot_path.resolve()}?mode=ro", uri=True,
            ) as connection:
                row = connection.execute(
                    """SELECT
                        (SELECT MAX(ts_ms) FROM feedback),
                        (SELECT MAX(completed_at_ms)
                         FROM feedback_source_coverage)""",
                ).fetchone()
        except sqlite3.Error as exc:
            raise RunVerificationError("invalid_source_snapshot") from exc
        actual_max_ts_ms = row[0] if row is not None else None
        actual_watermark = row[1] if row is not None else None
        if (
            actual_max_ts_ms != snapshot_meta.get("max_ts_ms")
            or isinstance(actual_watermark, bool)
            or not isinstance(actual_watermark, int)
            or actual_watermark != cutoff
        ):
            raise RunVerificationError("source_watermark_mismatch")
    return cutoff


def _validate_run_identity(
    run: Mapping[str, Any],
    spec: TopicSpec,
    source_watermark_ms: int,
) -> None:
    persisted_hash = run.get("spec_hash")
    expected_hash = topic_spec_hash(spec)
    expected_run_id = hashlib.sha256(
        f"{expected_hash}:{source_watermark_ms}".encode("utf-8"),
    ).hexdigest()[:16]
    if (
        persisted_hash == expected_hash
        and run.get("run_id") == expected_run_id
    ):
        return
    if _is_pre_mode_run_identity(run, spec, source_watermark_ms):
        return
    raise RunVerificationError("run_identity_mismatch")


def _is_pre_mode_run_identity(
    run: Mapping[str, Any],
    spec: TopicSpec,
    source_watermark_ms: int,
) -> bool:
    """Accept the exact identity used by canonical specs persisted before mode."""
    try:
        raw_spec = json.loads(run["spec_json"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(raw_spec, Mapping) or "mode" in raw_spec:
        return False
    old_canonical = spec.to_dict()
    old_canonical.pop("mode")
    old_hash = hashlib.sha256(
        json.dumps(
            old_canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()
    old_run_id = hashlib.sha256(
        f"{old_hash}:{source_watermark_ms}".encode("utf-8"),
    ).hexdigest()[:16]
    return run.get("spec_hash") == old_hash and run.get("run_id") == old_run_id


def _require_review_decisions(
    queue: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
) -> None:
    queue_ids = [row.get("item_id") for row in queue]
    decision_ids = [row.get("item_id") for row in decisions]
    if (
        any(not isinstance(value, str) or not value for value in queue_ids)
        or len(queue_ids) != len(set(queue_ids))
        or any(not isinstance(value, str) or not value for value in decision_ids)
        or len(decision_ids) != len(set(decision_ids))
        or set(queue_ids) != set(decision_ids)
    ):
        raise RunVerificationError("review_decisions_incomplete")


def _authoritative_evidence_sources(
    recalls: Sequence[RecallHit],
    contexts: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    sources: dict[str, tuple[str, ...]] = {}
    for hit in recalls:
        if hit.item_id in sources:
            raise RunVerificationError("duplicate_item_id")
        text = hit.item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RunVerificationError("missing_text")
        sources[hit.item_id] = (text, *_context_texts(contexts, hit.item_id))
    return sources


def _item_in_scope(item: Mapping[str, Any], spec: TopicSpec) -> bool:
    try:
        timestamp = int(item.get("ts_ms"))
    except (TypeError, ValueError):
        return False
    start = int(spec.scope.start_time.astimezone(timezone.utc).timestamp() * 1000)
    end = int(spec.scope.end_time.astimezone(timezone.utc).timestamp() * 1000)
    if not start <= timestamp < end:
        return False
    for item_key, values in (("platform", spec.scope.platforms), ("channel", spec.scope.channels), ("appversion", spec.scope.versions)):
        if values and item.get(item_key) not in values:
            return False
    return True


def _valid_source_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname or parsed.username is not None:
        return False
    # Accessing ``port`` above validates malformed values such as ``:bad``.
    return port is None or 0 < port <= 65535


def _context_texts(contexts: Mapping[str, Any], item_id: str) -> list[str]:
    values = contexts.get(item_id, []) if isinstance(contexts, Mapping) else []
    if not isinstance(values, list):
        return []
    return [str(row.get("text") or row.get("feedback_text")) for row in values if isinstance(row, Mapping) and isinstance(row.get("text") or row.get("feedback_text"), str)]


def _verify_manifest(manifest: Mapping[str, Any], artifact_dir: Path) -> None:
    mirror = artifact_dir / "manifest.json"
    try:
        mirror_value = json.loads(mirror.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        mirror_value = None
    if mirror_value != manifest:
        try:
            _atomic_json(mirror, manifest)
        except OSError as exc:
            raise RunVerificationError("manifest_hash_reconciliation") from exc
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        raise RunVerificationError("manifest_hash_reconciliation")
    for name, expected in artifacts.items():
        if not isinstance(name, str) or not isinstance(expected, str) or "/" in name or "\\" in name:
            raise RunVerificationError("manifest_hash_reconciliation")
        path = artifact_dir / name
        if not path.is_file() or _sha256(path) != expected:
            raise RunVerificationError("manifest_hash_reconciliation")


def _require_artifact(manifest: Mapping[str, Any], path: Path) -> None:
    if not _artifact_valid(manifest, path):
        raise RunVerificationError("manifest_hash_reconciliation")


def _require_stage_chain(manifest: Mapping[str, Any], artifact_dir: Path) -> None:
    stages = manifest.get("stages")
    if manifest.get("manifest_version") != _MANIFEST_VERSION or not isinstance(stages, Mapping):
        raise RunVerificationError("manifest_stage_chain")
    for stage in _STAGES:
        record = stages.get(stage)
        if not isinstance(record, Mapping) or not isinstance(record.get("inputs"), Mapping) or not isinstance(record.get("outputs"), Mapping):
            raise RunVerificationError("manifest_stage_chain")
        expected_inputs, expected_outputs = _stage_contract_names(stage, artifact_dir)
        if set(record["inputs"]) != expected_inputs or set(record["outputs"]) != expected_outputs:
            raise RunVerificationError("manifest_stage_chain")
        for name, expected in {**record["inputs"], **record["outputs"]}.items():
            path = artifact_dir / str(name)
            if not isinstance(expected, str) or not path.is_file() or _sha256(path) != expected:
                raise RunVerificationError("manifest_stage_chain")
        if int(record.get("output_count", -1)) != _stage_output_count(stage, artifact_dir, manifest):
            raise RunVerificationError("manifest_stage_chain")
        if int(record.get("input_count", -1)) != _stage_input_count(stage, artifact_dir):
            raise RunVerificationError("manifest_stage_chain")


def _checkpoint(
    run_id: str,
    store: TopicRunStore,
    artifact_dir: Path,
    manifest: dict[str, Any],
    stage: str,
    files: Sequence[Path],
    *,
    output_hashes: Mapping[str, str] | None = None,
) -> None:
    artifacts = manifest.setdefault("artifacts", {})
    for path in files:
        if not path.is_file():
            continue
        artifacts[path.name] = (
            output_hashes[path.name]
            if output_hashes is not None and path.name in output_hashes
            else _sha256(path)
        )
    if stage not in _STAGES:
        manifest["stage"] = stage
        _write_manifest_mirror(run_id, store, artifact_dir, manifest)
        store.update_manifest(run_id, manifest, stage=stage)
        return
    input_names, output_names = _stage_contract_names(stage, artifact_dir)
    inputs = _trusted_stage_input_hashes(
        manifest, artifact_dir, input_names,
    ) if input_names else {}
    if output_hashes is None:
        outputs = {name: _sha256(artifact_dir / name) for name in output_names}
    else:
        if set(output_hashes) != output_names:
            raise RunVerificationError("manifest_stage_chain")
        outputs = {
            name: digest
            for name, digest in output_hashes.items()
            if isinstance(digest, str)
            and len(digest) == 64
            and (artifact_dir / name).is_file()
        }
        if set(outputs) != output_names:
            raise RunVerificationError("manifest_stage_chain")
    funnel = manifest.setdefault("funnel", {})
    output_count = _stage_output_count(stage, artifact_dir, manifest)
    manifest.setdefault("stages", {})[stage] = {
        "inputs": inputs,
        "outputs": outputs,
        "input_count": _stage_input_count(stage, artifact_dir),
        "output_count": output_count,
    }
    if stage == "classify":
        manifest.get("stage_attempts", {}).pop("classify", None)
    funnel[stage + "_count"] = output_count
    manifest["manifest_version"] = _MANIFEST_VERSION
    manifest["stage"] = stage
    _write_manifest_mirror(run_id, store, artifact_dir, manifest)
    store.update_manifest(run_id, manifest, stage=stage)


def _prepare_checkpoint_manifest(
    manifest: dict[str, Any],
    artifact_dir: Path,
    stage: str,
    pending_hashes: Mapping[str, str],
) -> None:
    """Update a manifest before its files are terminal-published."""
    artifacts = manifest.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        raise RunVerificationError("manifest_hash_reconciliation")
    artifacts.update(pending_hashes)
    if stage not in _STAGES:
        manifest["stage"] = stage
        return
    input_names, output_names = _stage_contract_names(stage, artifact_dir)
    inputs = _trusted_stage_input_hashes(
        manifest,
        artifact_dir,
        input_names,
        pending_hashes=pending_hashes,
    ) if input_names else {}
    outputs: dict[str, str] = {}
    for name in output_names:
        if name in pending_hashes:
            outputs[name] = pending_hashes[name]
        else:
            path = artifact_dir / name
            if not path.is_file():
                raise RunVerificationError("manifest_stage_chain")
            outputs[name] = _sha256(path)
    output_count = _stage_output_count(stage, artifact_dir, manifest)
    manifest.setdefault("stages", {})[stage] = {
        "inputs": inputs,
        "outputs": outputs,
        "input_count": _stage_input_count(stage, artifact_dir),
        "output_count": output_count,
    }
    manifest.setdefault("funnel", {})[stage + "_count"] = output_count
    manifest["manifest_version"] = _MANIFEST_VERSION
    manifest["stage"] = stage


def _publish_terminal_mutation(
    run: Mapping[str, Any],
    store: TopicRunStore,
    manifest: Mapping[str, Any],
    *,
    stage: str,
    status: str | None,
    files: Mapping[str, bytes],
    delete_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    try:
        return store.publish_terminal_artifacts(
            str(run["run_id"]),
            expected_manifest_json=str(run.get("manifest_json") or "{}"),
            manifest=manifest,
            stage=stage,
            status=status,
            files=files,
            delete_names=delete_names,
        )
    except RunPublicationConflictError as exc:
        raise RunVerificationError("run_changed_retry") from exc


def _checkpoint_attempt(
    run_id: str,
    store: TopicRunStore,
    artifact_dir: Path,
    manifest: dict[str, Any],
    stage: str,
    files: Sequence[Path],
    *,
    output_names: Sequence[str] | None = None,
    output_hashes: Mapping[str, str] | None = None,
) -> None:
    artifacts = manifest.setdefault("artifacts", {})
    for path in files:
        if path.is_file():
            artifacts[path.name] = (
                output_hashes[path.name]
                if output_hashes is not None and path.name in output_hashes
                else _sha256(path)
            )
    inputs, _ = _stage_contract_names(stage, artifact_dir)
    if output_hashes is not None:
        outputs = {
            name: digest
            for name, digest in output_hashes.items()
            if _classification_output_name_safe(name)
            and isinstance(digest, str)
            and len(digest) == 64
            and (artifact_dir / name).is_file()
        }
    else:
        names = output_names if output_names is not None else (
            "classification_batches.jsonl",
            "classification_batches.jsonl.checkpoint.jsonl",
            "classification_batches.jsonl.run_state.json",
            "classification_batches.jsonl.failures.jsonl",
            "classification_audit.jsonl",
            "classified.jsonl",
        )
        outputs = {
            name: _sha256(artifact_dir / name)
            for name in names
            if _classification_output_name_safe(name)
            and (artifact_dir / name).is_file()
        }
    trusted_inputs = _trusted_stage_input_hashes(
        manifest, artifact_dir, inputs,
    )
    manifest.setdefault("stage_attempts", {})[stage] = {"inputs": trusted_inputs, "outputs": outputs, "input_count": _stage_input_count(stage, artifact_dir), "complete": False}
    manifest["stage"] = stage
    _write_manifest_mirror(run_id, store, artifact_dir, manifest)
    store.update_manifest(run_id, manifest, stage=stage)


def _load_manifest(run: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    raw = run.get("manifest_json") or "{}"
    try:
        manifest = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    # The database record is the durable trust anchor.  The on-disk manifest is
    # only a mirror checked by verification/export/download; it must never be
    # allowed to replace a persisted record after a partial write or tampering.
    return manifest


def _artifact_valid(manifest: Mapping[str, Any], path: Path) -> bool:
    expected = manifest.get("artifacts", {}).get(path.name) if isinstance(manifest.get("artifacts"), Mapping) else None
    return isinstance(expected, str) and path.is_file() and _sha256(path) == expected


def _stage_valid(manifest: Mapping[str, Any], stage: str, artifact_dir: Path) -> bool:
    record = manifest.get("stages", {}).get(stage) if isinstance(manifest.get("stages"), Mapping) else None
    if not isinstance(record, Mapping):
        return False
    expected_inputs, expected_outputs = _stage_contract_names(stage, artifact_dir)
    if set(record.get("inputs", {})) != expected_inputs or set(record.get("outputs", {})) != expected_outputs:
        return False
    for section in ("inputs", "outputs"):
        values = record.get(section)
        if not isinstance(values, Mapping):
            return False
        for name, expected in values.items():
            path = artifact_dir / str(name)
            if not isinstance(expected, str) or not path.is_file() or _sha256(path) != expected:
                return False
    if int(record.get("output_count", -1)) != _stage_output_count(stage, artifact_dir, manifest):
        return False
    if int(record.get("input_count", -1)) != _stage_input_count(stage, artifact_dir):
        return False
    return True


def _classification_resume_safe(manifest: Mapping[str, Any], artifact_dir: Path) -> bool:
    """Only reuse scheduler checkpoints when the exact classifier inputs match."""
    attempt_record, record = _classification_records(manifest)
    if not isinstance(record, Mapping) or not isinstance(record.get("inputs"), Mapping) or not isinstance(record.get("outputs"), Mapping):
        return False
    required_inputs, _required_outputs = _stage_contract_names(
        "classify", artifact_dir,
    )
    if set(record["inputs"]) != required_inputs:
        return False
    for name, expected in record["inputs"].items():
        path = artifact_dir / name
        if not path.is_file() or _sha256(path) != expected:
            return False
    # classified can be absent: scheduler checkpoint may safely reconstruct it.
    for name, expected in record["outputs"].items():
        normalized_name = str(name)
        if normalized_name == "classified.jsonl":
            continue
        if not _classification_output_name_safe(normalized_name):
            return False
        path = artifact_dir / normalized_name
        if not isinstance(expected, str) or not path.is_file() or _sha256(path) != expected:
            return False
    if attempt_record is not None:
        required_attempt_outputs = {
            "classification_batches.jsonl.checkpoint.jsonl",
            "classification_batches.jsonl.run_state.json",
        }
        if attempt_record.get("complete") is not False or not required_attempt_outputs <= set(record["outputs"]):
            return False
    return True


def _classification_records(
    manifest: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    attempts = manifest.get("stage_attempts", {}) if isinstance(manifest.get("stage_attempts"), Mapping) else {}
    attempt = attempts.get("classify")
    stages = manifest.get("stages", {}) if isinstance(manifest.get("stages"), Mapping) else {}
    stage = stages.get("classify")
    return (
        attempt if isinstance(attempt, Mapping) else None,
        attempt if isinstance(attempt, Mapping) else stage if isinstance(stage, Mapping) else None,
    )


def _trusted_stage_input_hashes(
    manifest: Mapping[str, Any],
    artifact_dir: Path,
    names: Sequence[str],
    *,
    pending_hashes: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Reuse prior stage hashes; never re-authenticate mutable canonical input."""
    stages = manifest.get("stages", {})
    if not isinstance(stages, Mapping):
        raise RunVerificationError("manifest_stage_chain")
    trusted: dict[str, str] = {}
    for name in names:
        expected: str | None = None
        for stage in reversed(_STAGES):
            record = stages.get(stage)
            outputs = record.get("outputs") if isinstance(record, Mapping) else None
            value = outputs.get(name) if isinstance(outputs, Mapping) else None
            if isinstance(value, str):
                expected = value
                break
        path = artifact_dir / name
        pending = pending_hashes.get(name) if pending_hashes is not None else None
        if expected is None or (
            pending != expected
            and (not path.is_file() or _sha256(path) != expected)
        ):
            raise RunVerificationError("manifest_stage_chain")
        trusted[name] = expected
    return trusted


def _classification_output_name_safe(name: str) -> bool:
    if name in _CLASSIFICATION_WORK_FILES:
        return True
    parts = Path(name).parts
    return (
        len(parts) == 2
        and parts[0] == "classification_partial_audit"
        and parts[1].endswith(".json")
        and parts[1] not in {".json", "..json"}
    )


def _classification_output_safe(
    manifest: Mapping[str, Any],
    artifact_dir: Path,
    name: str,
) -> bool:
    _attempt, record = _classification_records(manifest)
    outputs = record.get("outputs") if isinstance(record, Mapping) else None
    expected = outputs.get(name) if isinstance(outputs, Mapping) else None
    path = artifact_dir / name
    return (
        _classification_output_name_safe(name)
        and isinstance(expected, str)
        and path.is_file()
        and _sha256(path) == expected
    )


def _authenticated_classification_partials(
    manifest: Mapping[str, Any],
    artifact_dir: Path,
) -> tuple[str, ...]:
    _attempt, record = _classification_records(manifest)
    outputs = record.get("outputs") if isinstance(record, Mapping) else None
    if not isinstance(outputs, Mapping):
        return ()
    return tuple(
        name for name in sorted(str(value) for value in outputs)
        if name.startswith("classification_partial_audit/")
        and _classification_output_safe(manifest, artifact_dir, name)
    )


def _purge_classification_checkpoints(artifact_dir: Path) -> None:
    for name in (
        "classification_batches.jsonl", "classification_batches.jsonl.checkpoint.jsonl",
        "classification_batches.jsonl.failures.jsonl", "classification_batches.jsonl.run_state.json",
        "classification_audit.jsonl", "classified.jsonl",
    ):
        (artifact_dir / name).unlink(missing_ok=True)
    partial = artifact_dir / "classification_partial_audit"
    if partial.is_dir():
        for child in partial.iterdir():
            child.unlink(missing_ok=True)
        partial.rmdir()


def _stage_workspace(store: Any, artifact_dir: Path, stage: str) -> Path:
    """Return a claim-private workspace when the caller is a leased worker."""
    factory = getattr(store, "stage_artifact_dir", None)
    if factory is None:
        return artifact_dir
    return Path(factory(artifact_dir, stage))


def _promote_stage_files(
    run_id: str,
    store: Any,
    artifact_dir: Path,
    workspace: Path,
    names: Sequence[str],
) -> dict[str, str] | None:
    """Publish one worker generation only while its claim remains current."""
    if workspace == artifact_dir:
        return None
    _ensure_worker_active(run_id, store)
    publisher = getattr(store, "publish_worker_files", None)
    if publisher is None:  # pragma: no cover - paired with _stage_workspace
        raise RuntimeError("worker artifact publisher unavailable")
    pairs = [
        (workspace / name, artifact_dir / name)
        for name in names
    ]
    published = publisher(
        run_id,
        pairs,
    )
    if not isinstance(published, Mapping):  # pragma: no cover - store contract
        raise RuntimeError("worker artifact publisher omitted digests")
    hashes = {
        str(target.relative_to(artifact_dir)): str(
            published[Path(target).resolve()]
        )
        for _source, target in pairs
    }
    shutil.rmtree(workspace, ignore_errors=True)
    return hashes


def _publish_classification_progress(
    run_id: str,
    store: Any,
    artifact_dir: Path,
    workspace: Path,
    manifest: dict[str, Any],
) -> None:
    """Fence each fsync'd model checkpoint without exposing a live file handle."""
    if workspace == artifact_dir:
        return
    _ensure_worker_active(run_id, store)
    publisher = getattr(store, "publish_worker_files", None)
    if publisher is None:  # pragma: no cover - paired with _stage_workspace
        raise RuntimeError("worker artifact publisher unavailable")
    pairs = [
        (workspace / name, artifact_dir / name)
        for name in _CLASSIFICATION_WORK_FILES
        if (workspace / name).is_file()
    ]
    partial_dir = workspace / "classification_partial_audit"
    if partial_dir.is_dir():
        pairs.extend(
            (path, artifact_dir / partial_dir.name / path.name)
            for path in sorted(partial_dir.glob("*.json"))
            if path.is_file()
        )
    published: Mapping[Path, str] = publisher(run_id, pairs) if pairs else {}
    if not isinstance(published, Mapping):  # pragma: no cover - store contract
        raise RuntimeError("worker artifact publisher omitted digests")
    output_hashes = {
        str(target.relative_to(artifact_dir)): str(
            published[Path(target).resolve()]
        )
        for _source, target in pairs
    }
    # Authenticate each published scheduler checkpoint in the database-backed
    # manifest. A replacement worker may reuse only this fenced generation.
    _checkpoint_attempt(
        run_id,
        store,
        artifact_dir,
        manifest,
        "classify",
        (),
        output_names=tuple(output_hashes),
        output_hashes=output_hashes,
    )


def _seed_classification_workspace(
    artifact_dir: Path,
    workspace: Path,
    *,
    resume: bool,
    preserve_audit: bool,
    partial_audits: Sequence[str],
) -> None:
    """Copy authenticated audit history and any safely reusable scheduler state."""
    if workspace == artifact_dir:
        return
    # Audit history is append-only evidence and remains valid even when a
    # missing or mismatched scheduler checkpoint makes batch reuse unsafe.
    audit_source = artifact_dir / "classification_audit.jsonl"
    if preserve_audit:
        shutil.copy2(audit_source, workspace / audit_source.name)
    for name in partial_audits:
        source = artifact_dir / name
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if not resume:
        return
    for name in _CLASSIFICATION_WORK_FILES:
        if name == "classification_audit.jsonl":
            continue
        source = artifact_dir / name
        if source.is_file():
            shutil.copy2(source, workspace / name)


def _write_manifest_mirror(
    run_id: str,
    store: Any,
    artifact_dir: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Fence the on-disk manifest mirror with the same claim as the DB row."""
    workspace = _stage_workspace(store, artifact_dir, "manifest")
    _atomic_json(workspace / "manifest.json", manifest)
    _promote_stage_files(
        run_id, store, artifact_dir, workspace, ["manifest.json"],
    )


def _stage_contract_names(stage: str, artifact_dir: Path) -> tuple[set[str], set[str]]:
    contracts = {
        "snapshot": (set(), {"source_snapshot.sqlite"}),
        "hard_scope": ({"source_snapshot.sqlite"}, {"scoped_items.jsonl", "item_contexts.json"}),
        "hybrid_recall": ({"scoped_items.jsonl", "item_contexts.json"}, {"recall_candidates.jsonl", "recall_manifest.json"}),
        "classify": ({"recall_candidates.jsonl", "recall_manifest.json", "item_contexts.json"}, {"classified.jsonl", "classification_audit.jsonl"}),
        "review_queue": ({"classified.jsonl", "recall_candidates.jsonl", "classification_audit.jsonl"}, {"review_queue.jsonl", "review_overrides.jsonl"}),
        "review_ready": ({"review_queue.jsonl", "review_overrides.jsonl"}, {"review_ready.json"}),
    }
    inputs, outputs = contracts[stage]
    selected_path = artifact_dir / "selected_candidates.jsonl"
    if selected_path.is_file():
        if stage == "hybrid_recall":
            outputs.add(selected_path.name)
        elif stage == "classify":
            inputs.add(selected_path.name)
    if stage == "classify":
        for name in ("classification_batches.jsonl", "classification_batches.jsonl.checkpoint.jsonl", "classification_batches.jsonl.failures.jsonl", "classification_batches.jsonl.run_state.json"):
            if (artifact_dir / name).is_file():
                outputs.add(name)
    return set(inputs), set(outputs)


def _stage_output_count(stage: str, artifact_dir: Path, manifest: Mapping[str, Any]) -> int:
    if stage == "snapshot":
        return _snapshot_row_count(artifact_dir)
    if stage == "hard_scope":
        return len(_read_jsonl(artifact_dir / "scoped_items.jsonl"))
    if stage == "hybrid_recall":
        return len(_read_jsonl(artifact_dir / "recall_candidates.jsonl"))
    if stage == "classify":
        return len(_read_jsonl(artifact_dir / "classified.jsonl"))
    if stage == "review_queue":
        return len(_read_jsonl(artifact_dir / "review_queue.jsonl"))
    if stage == "review_ready":
        return len(_read_jsonl(artifact_dir / "classified.jsonl"))
    return len(_read_jsonl(artifact_dir / "final_reviewed.jsonl"))


def _stage_input_count(stage: str, artifact_dir: Path) -> int:
    if stage == "snapshot":
        return 0
    if stage == "hard_scope":
        return _snapshot_row_count(artifact_dir)
    if stage == "classify" and (artifact_dir / "selected_candidates.jsonl").is_file():
        return len(_read_jsonl(artifact_dir / "selected_candidates.jsonl"))
    previous = _STAGES[_STAGES.index(stage) - 1]
    return _stage_output_count(previous, artifact_dir, {})


def _snapshot_row_count(artifact_dir: Path) -> int:
    import sqlite3
    try:
        with sqlite3.connect(f"file:{(artifact_dir / 'source_snapshot.sqlite').resolve()}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT COUNT(*) FROM feedback").fetchone()
    except sqlite3.Error as exc:
        raise RunVerificationError("invalid_artifact") from exc
    if row is None or isinstance(row[0], bool) or not isinstance(row[0], int):
        raise RunVerificationError("invalid_artifact")
    return int(row[0])


def _read_required_jsonl(path: Path, manifest: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    raw = read_verified_artifact_bytes(manifest, path) if manifest is not None else _read_verified_bytes_from_path(path)
    try:
        values = []
        for line in raw.decode("utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("not object")
                values.append(value)
        return values
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RunVerificationError("invalid_artifact") from exc


def _read_required_json(path: Path, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        raw = read_verified_artifact_bytes(manifest, path) if manifest is not None else _read_verified_bytes_from_path(path)
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunVerificationError("invalid_artifact") from exc
    if not isinstance(value, dict):
        raise RunVerificationError("invalid_artifact")
    return value


def read_verified_artifact_bytes(manifest: Mapping[str, Any], path: Path) -> bytes:
    """Read once and validate those exact bytes against the DB manifest."""
    expected = manifest.get("artifacts", {}).get(path.name) if isinstance(manifest.get("artifacts"), Mapping) else None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RunVerificationError("manifest_hash_reconciliation") from exc
    if not isinstance(expected, str) or hashlib.sha256(raw).hexdigest() != expected:
        raise RunVerificationError("manifest_hash_reconciliation")
    return raw


def _read_verified_bytes_from_path(path: Path) -> bytes:
    # Callers already used _require_artifact / manifest validation. The helper
    # still reads once; it is retained for internal parse error normalization.
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RunVerificationError("missing_artifact") from exc


def _invalidate_export_artifacts(manifest: dict[str, Any]) -> None:
    artifacts = manifest.setdefault("artifacts", {})
    for name in ("final_reviewed.jsonl", "final_results.jsonl", "quality_report.json", "feedback_list.xlsx"):
        artifacts.pop(name, None)
    manifest.pop("verified", None)


def _record_failure(run_id: str, store: TopicRunStore, exc: Exception, config: TopicMiningConfig) -> None:
    name = type(exc).__name__
    if name == "QuotaExhaustedError":
        _set_status(run_id, store, "paused_quota_exhausted", stage="classify", error_code="quota_exhausted", error_message="classifier quota exhausted")
        return
    if isinstance(exc, DataCoverageError):
        code = "data_coverage_error"
    elif isinstance(exc, SourceReadinessError):
        code = "source_readiness_error"
    elif name == "VectorIndexStaleError":
        code = "vector_index_stale"
    elif "unresolved_classifier_items" in str(exc):
        code = "classifier_unresolved"
    else:
        code = "topic_run_error"
    run = _require_run(run_id, store)
    manifest = _load_manifest(run, Path(run["artifact_dir"]))
    if code in {"data_coverage_error", "source_readiness_error"}:
        manifest["unresolved_coverage_items"] = 1
    if code == "vector_index_stale":
        manifest["unresolved_vector_items"] = 1
    _write_manifest_mirror(
        run_id, store, Path(run["artifact_dir"]), manifest,
    )
    store.update_manifest(run_id, manifest, stage=run["stage"])
    _set_status(run_id, store, "failed", error_code=code, error_message=_redact_message(str(exc), config))


def _set_status(run_id: str, store: TopicRunStore, status: str, *, stage: str | None = None, error_code: str | None = None, error_message: str | None = None) -> None:
    run = _require_run(run_id, store)
    manifest = _load_manifest(run, Path(run["artifact_dir"]))
    store.update_manifest(
        run_id, manifest, stage=stage or run["stage"], status=status,
        error_code=error_code, error_message=error_message,
    )


def _ensure_worker_active(run_id: str, store: Any) -> None:
    checker = getattr(store, "ensure_worker_claim", None)
    if checker is not None:
        checker(run_id)


def _require_run(run_id: str, store: TopicRunStore) -> dict[str, Any]:
    run = get_topic_run(run_id, store=store)
    if run is None:
        raise KeyError(run_id)
    return run


def _recall_from_dict(value: Mapping[str, Any]) -> RecallHit:
    try:
        if not isinstance(value, Mapping) or not isinstance(value["item"], Mapping):
            raise TypeError("invalid recall")
        return RecallHit(
            item_id=str(value["item_id"]), item=dict(value["item"]), channels=tuple(value.get("channels", [])),
            fused_score=float(value.get("fused_score", 0)), fused_rank=int(value.get("fused_rank", 0)),
            channel_ranks=dict(value.get("channel_ranks", {})), raw_scores=dict(value.get("raw_scores", {})),
            query_ids=tuple(value.get("query_ids", [])), negative_query_hits=tuple(value.get("negative_query_hits", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RunVerificationError("invalid_artifact") from exc


def _classification_from_dict(value: Mapping[str, Any]) -> ClassificationResult:
    try:
        if not isinstance(value, Mapping) or not isinstance(value.get("reason"), str):
            raise TypeError("invalid classification")
        return ClassificationResult(
            item_id=str(value["item_id"]), label=str(value["label"]), confidence=float(value["confidence"]),
            evidence=tuple(value.get("evidence", [])), reason=value["reason"], needs_review=bool(value.get("needs_review", False)),
            source=str(value.get("source", "classifier")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RunVerificationError("invalid_artifact") from exc


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_jsonl_bytes(rows))
    temporary.replace(path)


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    ).encode("utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RunVerificationError("invalid_artifact")
                values.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunVerificationError("invalid_artifact") from exc
    return values


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {path.name}") from exc


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_config(config: TopicMiningConfig) -> dict[str, Any]:
    values = asdict(config)
    for key in ("vector_api_token", "api_token"):
        values.pop(key, None)
    values["source_db_path"] = str(values["source_db_path"])
    values["data_dir"] = str(values["data_dir"])
    return values


def _safe_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _classification_quality(audit_path: Path) -> dict[str, Any]:
    routes: set[str] = set()
    models: set[str] = set()
    attempts = retries = 0
    for row in _read_jsonl(audit_path):
        if isinstance(row.get("route_source"), str):
            routes.add(row["route_source"])
        if isinstance(row.get("model"), str):
            models.add(row["model"])
        attempts += int(row.get("attempts", 0) or 0)
        retries += len(row.get("retry_chain", []) if isinstance(row.get("retry_chain"), list) else [])
    return {"routes": sorted(routes), "models": sorted(models), "attempts_total": attempts, "retry_total": retries}


def _redact_message(message: str, config: TopicMiningConfig) -> str:
    for secret in (config.api_token, config.vector_api_token):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    # LLM credentials are not part of TopicMiningConfig but must never leak
    # through a background status response.
    import os
    key = os.environ.get("LLM_API_KEY", "")
    if key:
        message = message.replace(key, "[REDACTED]")
    return message[:500]
