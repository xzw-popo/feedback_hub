"""Audited orchestration and verification for isolated topic-mining runs.

The source database is only ever handed to :mod:`source`, whose connection
helper opens it with SQLite's ``mode=ro`` URI.  Everything produced here lives
under the run artifact directory, so a failed or paused run is inspectable and
can resume without changing feedback records or formal labels.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .classifier import ClassificationResult, classify_candidates
from .config import TopicMiningConfig
from .contracts import TopicSpec, validate_topic_spec
from .retrieval import RecallHit, build_semantic_queries, build_vector_filters, hybrid_recall
from .review import apply_review_overrides, build_review_queue, persist_review_artifacts
from .run_store import TopicRunStore
from .source import DataCoverageError, build_item_contexts, create_source_snapshot, fetch_scoped_items
from .vector_client import HttpVectorSearchClient


class RunVerificationError(ValueError):
    """Raised when an audited run cannot safely become exportable."""


_STAGES = ("snapshot", "hard_scope", "hybrid_recall", "classify", "review_queue", "review_ready")
_UNRESOLVED_KEYS = (
    "unresolved_classifier_items", "unresolved_parser_items", "duplicate_item_ids",
    "missing_link_items", "unresolved_vector_items", "unresolved_coverage_items",
)


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
) -> dict[str, Any]:
    """Run (or resume) a topic job. External clients are injectable for E2E tests.

    A status update is persisted after every valid artifact.  The callback
    parameters deliberately make no lower-level retrieval/model tuning public.
    """
    config = config or TopicMiningConfig()
    store = store or default_store(config)
    run = _require_run(run_id, store)
    if run["status"] == "verified":
        return run
    try:
        _run_pipeline(
            run_id=run_id, store=store, config=config, vector_client=vector_client,
            classifier_routes=classifier_routes, classifier_call_fn=classifier_call_fn,
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
    _set_status(run_id, store, "running", stage=run.get("stage") or "created")

    snapshot_path = artifact_dir / "source_snapshot.sqlite"
    if not _artifact_valid(manifest, snapshot_path):
        snapshot = create_source_snapshot(config.source_db_path, snapshot_path)
        snapshot_data = asdict(snapshot)
        snapshot_data["path"] = str(snapshot.path)
        manifest.update({
            "source_snapshot": snapshot_data,
            "source_watermark_ms": snapshot.max_ts_ms,
            "source_sha256": snapshot.sha256,
        })
        _checkpoint(run_id, store, artifact_dir, manifest, "snapshot", [snapshot_path])
    snapshot_meta = manifest.get("source_snapshot") or {}
    source_watermark_ms = int(snapshot_meta.get("max_ts_ms") or run["source_watermark_ms"])

    scoped_path = artifact_dir / "scoped_items.jsonl"
    contexts_path = artifact_dir / "item_contexts.json"
    if not _artifact_valid(manifest, scoped_path) or not _artifact_valid(manifest, contexts_path):
        items = fetch_scoped_items(snapshot_path, spec)
        contexts = build_item_contexts(items, spec.unit, window_ms=1_800_000)
        _write_jsonl(scoped_path, items)
        _atomic_json(contexts_path, contexts)
        manifest["hard_scope"] = {"item_count": len(items), "unit": spec.unit}
        _checkpoint(run_id, store, artifact_dir, manifest, "hard_scope", [scoped_path, contexts_path])
    items = _read_jsonl(scoped_path)
    contexts = _read_json(contexts_path, {})

    recall_path = artifact_dir / "recall_candidates.jsonl"
    recall_manifest_path = artifact_dir / "recall_manifest.json"
    if not _artifact_valid(manifest, recall_path) or not _artifact_valid(manifest, recall_manifest_path):
        client = vector_client or HttpVectorSearchClient(config)
        capabilities = client.capabilities()
        if spec.unit not in capabilities.supported_units:
            raise ValueError("vector_index_unsupported_unit")
        result = client.search(build_semantic_queries(spec), build_vector_filters(spec), config.vector_top_k)
        plan = hybrid_recall(
            items, spec, vector_hits=result.hits, config=config, artifact_dir=artifact_dir,
            source_watermark_ms=source_watermark_ms, vector_watermark_ms=result.watermark_ts_ms,
        )
        manifest.update({
            "vector_watermark_ms": result.watermark_ts_ms,
            "vector_index": result.index,
            "hybrid_recall": {"candidate_count": len(plan.candidates), "rejected_out_of_scope_ids": list(plan.rejected_out_of_scope_ids)},
            "retrieval_config": _safe_config(config),
        })
        _checkpoint(run_id, store, artifact_dir, manifest, "hybrid_recall", [recall_path, recall_manifest_path])
    recalls = [_recall_from_dict(row) for row in _read_jsonl(recall_path)]

    classified_path = artifact_dir / "classified.jsonl"
    audit_path = artifact_dir / "classification_audit.jsonl"
    classifications_rebuilt = False
    if not _artifact_valid(manifest, classified_path):
        results, stats = classify_candidates(
            spec, recalls, artifact_dir=artifact_dir, contexts=contexts, routes=classifier_routes,
            config=config, resume=True, call_fn=classifier_call_fn,
        )
        manifest["classifier"] = _safe_json(stats)
        manifest["unresolved_classifier_items"] = max(0, len(recalls) - len(results))
        manifest["unresolved_parser_items"] = int(stats.get("failed", 0))
        # Classification can safely pause; it must never publish review_ready.
        if stats.get("run_status") == "paused_quota_exhausted":
            _checkpoint(run_id, store, artifact_dir, manifest, "classify", [audit_path])
            _set_status(run_id, store, "paused_quota_exhausted", stage="classify")
            return
        if manifest["unresolved_classifier_items"] or manifest["unresolved_parser_items"]:
            _checkpoint(run_id, store, artifact_dir, manifest, "classify", [audit_path])
            raise ValueError("unresolved_classifier_items")
        _checkpoint(run_id, store, artifact_dir, manifest, "classify", [classified_path, audit_path])
        classifications_rebuilt = True
    classifications = [_classification_from_dict(row) for row in _read_jsonl(classified_path)]

    queue_path = artifact_dir / "review_queue.jsonl"
    overrides_path = artifact_dir / "review_overrides.jsonl"
    if not overrides_path.exists():
        _write_jsonl(overrides_path, [])
    if classifications_rebuilt or not _artifact_valid(manifest, queue_path):
        queue = build_review_queue(run_id, classifications, {row.item_id: row for row in recalls})
        persist_review_artifacts(artifact_dir, queue, _read_jsonl(overrides_path))
        manifest["review_queue"] = {"item_count": len(queue)}
        _checkpoint(run_id, store, artifact_dir, manifest, "review_queue", [queue_path, overrides_path])

    # The final persisted stage says only that review material is ready. A
    # separately requested verify step is required before data leaves the run.
    manifest.setdefault("unresolved_classifier_items", 0)
    manifest.setdefault("unresolved_parser_items", 0)
    _checkpoint(run_id, store, artifact_dir, manifest, "review_ready", [queue_path, overrides_path])
    _set_status(run_id, store, "review_ready", stage="review_ready")


def submit_review_overrides(
    run_id: str,
    overrides: Sequence[Mapping[str, Any]],
    *,
    store: TopicRunStore | None = None,
) -> list[dict[str, Any]]:
    store = store or default_store()
    run = _require_run(run_id, store)
    artifact_dir = Path(run["artifact_dir"])
    classifications = [_classification_from_dict(row) for row in _read_jsonl(artifact_dir / "classified.jsonl")]
    spec = validate_topic_spec(json.loads(run["spec_json"]))
    # Validate before touching the persistent file. It is intentionally only a
    # decision layer; source text/evidence never comes from client input.
    apply_review_overrides(classifications, overrides, allowed_labels={entry["id"] for entry in spec.classification_labels})
    _write_jsonl(artifact_dir / "review_overrides.jsonl", [dict(row) for row in overrides])
    manifest = _load_manifest(run, artifact_dir)
    _checkpoint(run_id, store, artifact_dir, manifest, "review_ready", [artifact_dir / "review_overrides.jsonl"])
    return [dict(row) for row in overrides]


def verify_topic_run(run_id: str, *, store: TopicRunStore | None = None) -> dict[str, Any]:
    """Validate every membership claim and make an immutable verified result."""
    store = store or default_store()
    run = _require_run(run_id, store)
    artifact_dir = Path(run["artifact_dir"])
    manifest = _load_manifest(run, artifact_dir)
    _verify_manifest(manifest, artifact_dir)
    for key in _UNRESOLVED_KEYS:
        if int(manifest.get(key, 0) or 0) > 0:
            raise RunVerificationError(key)
    spec = validate_topic_spec(json.loads(run["spec_json"]))
    recalls = [_recall_from_dict(row) for row in _read_jsonl(artifact_dir / "recall_candidates.jsonl")]
    classifications = [_classification_from_dict(row) for row in _read_jsonl(artifact_dir / "classified.jsonl")]
    expected = {hit.item_id for hit in recalls}
    actual = [row.item_id for row in classifications]
    if len(actual) != len(set(actual)):
        raise RunVerificationError("duplicate_item_id")
    if set(actual) != expected:
        raise RunVerificationError("classification_coverage")
    allowed = {row["id"] for row in spec.classification_labels}
    if any(row.label not in allowed for row in classifications):
        raise RunVerificationError("invalid_label")
    by_id = {hit.item_id: hit.item for hit in recalls}
    for result in classifications:
        item = by_id[result.item_id]
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RunVerificationError("missing_text")
        if not isinstance(item.get("source_url"), str) or not item["source_url"].startswith(("https://", "http://")):
            raise RunVerificationError("missing_link")
        if result.label == "matched" and (not result.evidence or not all(evidence in text for evidence in result.evidence)):
            raise RunVerificationError("invalid_evidence")
        if not _item_in_scope(item, spec):
            raise RunVerificationError("invalid_scope")
    overrides = _read_jsonl(artifact_dir / "review_overrides.jsonl")
    merged = apply_review_overrides(classifications, overrides, allowed_labels=allowed)
    final_rows = [_final_row(result, by_id[result.item_id], run_id) for result in merged if result.label == "matched"]
    _validate_final_rows(final_rows, spec)
    final_path = artifact_dir / "final_reviewed.jsonl"
    _write_jsonl(final_path, final_rows)
    manifest["verified"] = {"matched_count": len(final_rows), "verified_at_ms": int(time.time() * 1000)}
    _checkpoint(run_id, store, artifact_dir, manifest, "verified", [final_path])
    _set_status(run_id, store, "verified", stage="verified")
    return {"run_id": run_id, "status": "verified", "matched_count": len(final_rows)}


def _final_row(result: ClassificationResult, item: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "item_id": result.item_id, "label": result.label, "confidence": result.confidence,
        "evidence": list(result.evidence), "reason": result.reason, "source": result.source,
        "source_item": dict(item), "run_id": run_id,
    }


def _validate_final_rows(rows: Sequence[Mapping[str, Any]], spec: TopicSpec) -> None:
    seen: set[str] = set()
    allowed = {entry["id"] for entry in spec.classification_labels}
    for row in rows:
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise RunVerificationError("duplicate_item_id")
        seen.add(item_id)
        if row.get("label") != "matched" or row["label"] not in allowed:
            raise RunVerificationError("invalid_label")
        item = row.get("source_item")
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise RunVerificationError("missing_text")
        url = item.get("source_url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise RunVerificationError("missing_link")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(value, str) and value in item["text"] for value in evidence):
            raise RunVerificationError("invalid_evidence")
        if not _item_in_scope(item, spec):
            raise RunVerificationError("invalid_scope")


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


def _verify_manifest(manifest: Mapping[str, Any], artifact_dir: Path) -> None:
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        raise RunVerificationError("manifest_hash_reconciliation")
    for name, expected in artifacts.items():
        if not isinstance(name, str) or not isinstance(expected, str) or "/" in name or "\\" in name:
            raise RunVerificationError("manifest_hash_reconciliation")
        path = artifact_dir / name
        if not path.is_file() or _sha256(path) != expected:
            raise RunVerificationError("manifest_hash_reconciliation")


def _checkpoint(run_id: str, store: TopicRunStore, artifact_dir: Path, manifest: dict[str, Any], stage: str, files: Sequence[Path]) -> None:
    artifacts = manifest.setdefault("artifacts", {})
    for path in files:
        if not path.is_file():
            continue
        artifacts[path.name] = _sha256(path)
    manifest["stage"] = stage
    _atomic_json(artifact_dir / "manifest.json", manifest)
    store.update_manifest(run_id, manifest, stage=stage)


def _load_manifest(run: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    raw = run.get("manifest_json") or "{}"
    try:
        manifest = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    disk = artifact_dir / "manifest.json"
    if disk.exists():
        try:
            disk_value = _read_json(disk, {})
            if isinstance(disk_value, dict):
                manifest = disk_value
        except ValueError:
            pass
    return manifest


def _artifact_valid(manifest: Mapping[str, Any], path: Path) -> bool:
    expected = manifest.get("artifacts", {}).get(path.name) if isinstance(manifest.get("artifacts"), Mapping) else None
    return isinstance(expected, str) and path.is_file() and _sha256(path) == expected


def _record_failure(run_id: str, store: TopicRunStore, exc: Exception, config: TopicMiningConfig) -> None:
    name = type(exc).__name__
    if name == "QuotaExhaustedError":
        _set_status(run_id, store, "paused_quota_exhausted", stage="classify", error_code="quota_exhausted", error_message="classifier quota exhausted")
        return
    if isinstance(exc, DataCoverageError):
        code = "data_coverage_error"
    elif name == "VectorIndexStaleError":
        code = "vector_index_stale"
    elif "unresolved_classifier_items" in str(exc):
        code = "classifier_unresolved"
    else:
        code = "topic_run_error"
    _set_status(run_id, store, "failed", error_code=code, error_message=_redact_message(str(exc), config))


def _set_status(run_id: str, store: TopicRunStore, status: str, *, stage: str | None = None, error_code: str | None = None, error_message: str | None = None) -> None:
    run = _require_run(run_id, store)
    manifest = _load_manifest(run, Path(run["artifact_dir"]))
    store.update_manifest(
        run_id, manifest, stage=stage or run["stage"], status=status,
        error_code=error_code, error_message=error_message,
    )


def _require_run(run_id: str, store: TopicRunStore) -> dict[str, Any]:
    run = get_topic_run(run_id, store=store)
    if run is None:
        raise KeyError(run_id)
    return run


def _recall_from_dict(value: Mapping[str, Any]) -> RecallHit:
    return RecallHit(
        item_id=str(value["item_id"]), item=dict(value["item"]), channels=tuple(value.get("channels", [])),
        fused_score=float(value.get("fused_score", 0)), fused_rank=int(value.get("fused_rank", 0)),
        channel_ranks=dict(value.get("channel_ranks", {})), raw_scores=dict(value.get("raw_scores", {})),
        query_ids=tuple(value.get("query_ids", [])), negative_query_hits=tuple(value.get("negative_query_hits", [])),
    )


def _classification_from_dict(value: Mapping[str, Any]) -> ClassificationResult:
    return ClassificationResult(
        item_id=str(value["item_id"]), label=str(value["label"]), confidence=float(value["confidence"]),
        evidence=tuple(value.get("evidence", [])), reason=str(value["reason"]), needs_review=bool(value.get("needs_review", False)),
        source=str(value.get("source", "classifier")),
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RunVerificationError("invalid_artifact")
            values.append(value)
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
