"""Deterministic review selection and immutable review overrides."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .classifier import ClassificationResult
from .retrieval import RecallHit


def build_review_queue(
    run_id: str,
    classifications: Sequence[ClassificationResult],
    recall_by_id: Mapping[str, RecallHit],
    *,
    per_label_sample: int = 20,
) -> list[dict[str, Any]]:
    if per_label_sample < 0 or per_label_sample > 20:
        raise ValueError("per_label_sample must be between 0 and 20")
    rows: dict[str, dict[str, Any]] = {}
    for result in classifications:
        if result.item_id in rows:
            raise ValueError(f"duplicate classification item_id: {result.item_id}")
        reasons: set[str] = set()
        hit = recall_by_id.get(result.item_id)
        if hit is None:
            raise ValueError(f"missing recall source for classification item_id: {result.item_id}")
        if result.needs_review:
            reasons.add("classifier_requested_review")
        if result.label == "matched":
            if result.confidence < 0.75:
                reasons.add("low_confidence_match")
            if not any(channel in {"bm25", "lexical"} for channel in hit.channels):
                reasons.add("vector_only_match")
            if hit.negative_query_hits:
                reasons.add("negative_query_conflict")
        rows[result.item_id] = _queue_row(result, hit, reasons)
    for label in sorted({result.label for result in classifications}):
        selected = _hash_select(run_id, [result for result in classifications if result.label == label], "deterministic_label_sample", per_label_sample)
        for result in selected:
            rows[result.item_id]["review_reasons"].append("deterministic_label_sample")
    rejects = [result for result in classifications if result.label == "not_matched" and result.confidence >= 0.85]
    for result in _hash_select(run_id, rejects, "high_confidence_reject_sample", 20):
        rows[result.item_id]["review_reasons"].append("high_confidence_reject_sample")
    queue = []
    for item_id in sorted(rows):
        row = rows[item_id]
        row["review_reasons"] = sorted(set(row["review_reasons"]))
        if row["review_reasons"]:
            queue.append(row)
    return queue


def apply_review_overrides(
    classifications: Sequence[ClassificationResult],
    overrides: Sequence[Mapping[str, Any]],
    *,
    allowed_labels: set[str] | None = None,
) -> list[ClassificationResult]:
    allowed_labels = {"matched", "not_matched"} if allowed_labels is None else allowed_labels
    original = {result.item_id: result for result in classifications}
    seen: set[str] = set()
    validated: dict[str, Mapping[str, Any]] = {}
    for override in overrides:
        item_id = _required_override_text(override.get("item_id"), "item_id")
        if item_id in seen:
            raise ValueError(f"duplicate override item_id: {item_id}")
        seen.add(item_id)
        if item_id not in original:
            raise ValueError(f"unknown override item_id: {item_id}")
        label = _required_override_text(override.get("label"), "label")
        if label not in allowed_labels:
            raise ValueError(f"unsupported override label: {label}")
        _required_override_text(override.get("reason"), "reason")
        _required_override_text(override.get("reviewer"), "reviewer")
        if label == "matched" and not original[item_id].evidence:
            raise ValueError("override cannot mark an empty-evidence result as matched")
        validated[item_id] = override
    merged = []
    for result in classifications:
        override = validated.get(result.item_id)
        if override is None:
            merged.append(result)
        else:
            merged.append(replace(
                result, label=str(override["label"]).strip(), reason=str(override["reason"]).strip(),
                source="review_override",
            ))
    return merged


def write_review_queue(path: Path, queue: Sequence[Mapping[str, Any]]) -> None:
    _write_jsonl(path, queue)


def write_review_overrides(path: Path, overrides: Sequence[Mapping[str, Any]]) -> None:
    _write_jsonl(path, overrides)


def persist_review_artifacts(
    artifact_dir: Path,
    queue: Sequence[Mapping[str, Any]],
    overrides: Sequence[Mapping[str, Any]],
) -> None:
    """Persist the pending review work and submitted decisions independently."""
    artifact_dir = Path(artifact_dir)
    write_review_queue(artifact_dir / "review_queue.jsonl", queue)
    write_review_overrides(artifact_dir / "review_overrides.jsonl", overrides)


def _queue_row(result: ClassificationResult, hit: RecallHit | None, reasons: set[str]) -> dict[str, Any]:
    source_item = dict(hit.item) if hit is not None else {}
    return {
        "item_id": result.item_id,
        "label": result.label,
        "confidence": result.confidence,
        "evidence": list(result.evidence),
        "reason": result.reason,
        "needs_review": result.needs_review,
        "classification_source": result.source,
        "review_reasons": sorted(reasons),
        "source_item": source_item,
        "source_url": source_item.get("source_url"),
    }


def _hash_select(run_id: str, values: Sequence[ClassificationResult], reason: str, limit: int) -> list[ClassificationResult]:
    return sorted(values, key=lambda value: (hashlib.sha256(f"{run_id}:{value.item_id}:{reason}".encode("utf-8")).hexdigest(), value.item_id))[:limit]


def _required_override_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"override {field} must be non-empty")
    return value.strip()


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
