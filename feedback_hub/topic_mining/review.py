"""Deterministic review selection and immutable review overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .classifier import ClassificationResult
from .retrieval import RecallHit


@dataclass(frozen=True)
class ReviewQueuePlan:
    rows: tuple[dict[str, Any], ...]
    mandatory_count: int
    sample_limit: int
    sampled_count: int


def plan_review_queue(
    run_id: str,
    classifications: Sequence[ClassificationResult],
    recall_by_id: Mapping[str, RecallHit],
    *,
    contexts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    sample_limit: int,
) -> ReviewQueuePlan:
    if sample_limit < 0:
        raise ValueError("sample_limit must not be negative")
    rows: dict[str, dict[str, Any]] = {}
    mandatory_ids: set[str] = set()
    strata: dict[str, list[str]] = {
        "vector_only_match": [],
        "negative_query_conflict": [],
        "deterministic_label_sample": [],
        "high_confidence_reject_sample": [],
    }
    for result in classifications:
        if result.item_id in rows:
            raise ValueError(f"duplicate classification item_id: {result.item_id}")
        reasons: set[str] = set()
        hit = recall_by_id.get(result.item_id)
        if hit is None:
            raise ValueError(f"missing recall source for classification item_id: {result.item_id}")
        if result.needs_review:
            reasons.add("classifier_requested_review")
            mandatory_ids.add(result.item_id)
        if result.label == "matched":
            if result.confidence < 0.75:
                reasons.add("low_confidence_match")
                mandatory_ids.add(result.item_id)
            vector_only = not any(
                channel in {"bm25", "lexical"} for channel in hit.channels
            )
            negative_conflict = bool(hit.negative_query_hits)
            if vector_only:
                reasons.add("vector_only_match")
                strata["vector_only_match"].append(result.item_id)
            if negative_conflict:
                reasons.add("negative_query_conflict")
                strata["negative_query_conflict"].append(result.item_id)
            if not vector_only and not negative_conflict:
                reasons.add("deterministic_label_sample")
                strata["deterministic_label_sample"].append(result.item_id)
        elif result.confidence >= 0.85:
            reasons.add("high_confidence_reject_sample")
            strata["high_confidence_reject_sample"].append(result.item_id)
        rows[result.item_id] = _queue_row(
            result, hit, reasons,
            () if contexts is None else contexts.get(result.item_id, ()),
        )

    selected_ids: set[str] = set()
    ordered: dict[str, list[str]] = {}
    for name, item_ids in strata.items():
        ordered[name] = sorted(
            (item_id for item_id in item_ids if item_id not in mandatory_ids),
            key=lambda item_id: _sample_key(run_id, item_id, name),
        )
    positions = {name: 0 for name in ordered}
    while len(selected_ids) < sample_limit:
        selected_this_round = False
        for name, item_ids in ordered.items():
            position = positions[name]
            while position < len(item_ids) and item_ids[position] in selected_ids:
                position += 1
            positions[name] = position
            if position >= len(item_ids):
                continue
            selected_ids.add(item_ids[position])
            positions[name] += 1
            selected_this_round = True
            if len(selected_ids) == sample_limit:
                break
        if not selected_this_round:
            break

    queue_ids = mandatory_ids | selected_ids
    queue = tuple(rows[item_id] for item_id in sorted(queue_ids))
    return ReviewQueuePlan(
        rows=queue,
        mandatory_count=len(mandatory_ids),
        sample_limit=sample_limit,
        sampled_count=len(selected_ids),
    )


def apply_review_overrides(
    classifications: Sequence[ClassificationResult],
    overrides: Sequence[Mapping[str, Any]],
    *,
    allowed_labels: set[str] | None = None,
    evidence_sources: Mapping[str, Sequence[str]] | None = None,
) -> list[ClassificationResult]:
    allowed_labels = {"matched", "not_matched"} if allowed_labels is None else allowed_labels
    original = {result.item_id: result for result in classifications}
    seen: set[str] = set()
    validated: dict[str, tuple[Mapping[str, Any], tuple[str, ...]]] = {}
    for override in overrides:
        unsupported_fields = set(override) - {"item_id", "label", "reason", "reviewer", "evidence"}
        if unsupported_fields:
            raise ValueError(f"unsupported override field: {sorted(unsupported_fields)[0]}")
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
        effective_evidence = original[item_id].evidence
        if "evidence" in override:
            effective_evidence = _override_evidence(override["evidence"], item_id, evidence_sources)
        if label == "matched" and not effective_evidence:
            raise ValueError("matched override evidence must be non-empty")
        if evidence_sources is not None and effective_evidence:
            sources = evidence_sources.get(item_id, ())
            if not all(any(value in source for source in sources) for value in effective_evidence):
                raise ValueError("override evidence must be an exact source or context substring")
        validated[item_id] = (override, tuple(effective_evidence))
    merged = []
    for result in classifications:
        validated_override = validated.get(result.item_id)
        if validated_override is None:
            merged.append(result)
        else:
            override, effective_evidence = validated_override
            merged.append(replace(
                result, label=str(override["label"]).strip(), reason=str(override["reason"]).strip(),
                evidence=effective_evidence, source="review_override",
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


def _queue_row(
    result: ClassificationResult,
    hit: RecallHit | None,
    reasons: set[str],
    context_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
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
        "context_items": [dict(item) for item in context_items],
    }


def _sample_key(run_id: str, item_id: str, stratum: str) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{run_id}:{item_id}:{stratum}".encode("utf-8")
    ).hexdigest()
    return digest, item_id


def _required_override_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"override {field} must be non-empty")
    return value.strip()


def _override_evidence(
    value: Any,
    item_id: str,
    evidence_sources: Mapping[str, Sequence[str]] | None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(entry, str) and entry.strip() for entry in value):
        raise ValueError("override evidence must be a non-empty string list")
    if evidence_sources is None or item_id not in evidence_sources:
        raise ValueError("override evidence requires authoritative evidence sources")
    sources = evidence_sources[item_id]
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)) or not all(isinstance(source, str) for source in sources):
        raise ValueError("override evidence sources are invalid")
    evidence = tuple(value)
    if not all(any(entry in source for source in sources) for entry in evidence):
        raise ValueError("override evidence must be an exact source or context substring")
    return evidence


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
