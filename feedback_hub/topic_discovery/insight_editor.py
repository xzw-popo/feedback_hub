"""Relate atomic topics and run strict topic-level daily insight editing."""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import re
from typing import Any, Callable

import numpy as np

from feedback_hub.topic_discovery.model_routes import (
    ModelRoute,
    QuotaExhaustedError,
    call_model_route,
    invoke_model_route,
    normalize_model_routes,
    run_pauseable_model_jobs,
)


_RELATION_PRIORITY = {
    "same_stable_topic": 0,
    "parent_subtopic": 1,
    "semantic_similarity": 2,
}

_REPORT_DECISIONS = {"main", "observe", "exclude", "manual_review"}
_SIGNAL_TYPES = {
    "new_bug",
    "rising_or_repeated_bug",
    "demand_opportunity",
    "high_value_single",
    "not_reportable",
}
_TREND_CLAIMS = {"none", "new_signal", "repeated", "persistent", "reappeared", "rising"}
_FENCE_JSON_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def build_candidate_relation_plan(
    candidates: list[dict[str, Any]],
    daily_topic_ids: list[str],
    daily_embeddings: np.ndarray,
    *,
    threshold: float = 0.78,
    top_k: int = 5,
    max_bucket_size: int = 8,
) -> dict[str, Any]:
    """Build bounded report-only relation buckets for recalled candidates."""
    _validate_candidate_embeddings(candidates, daily_topic_ids, daily_embeddings)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if max_bucket_size < 2:
        raise ValueError("max_bucket_size must be at least 2")

    candidate_vectors = _candidate_vectors(candidates, daily_topic_ids, daily_embeddings)
    relations = _dedupe_relations([
        *_structural_relations(candidates),
        *_semantic_relations(candidates, candidate_vectors, threshold=threshold, top_k=top_k),
    ])
    buckets, singleton_candidate_ids = _bounded_components(
        candidates,
        relations,
        max_bucket_size=max_bucket_size,
    )
    covered = [
        str(item.get("candidate_id") or "")
        for bucket in buckets
        for item in bucket["items"]
    ] + singleton_candidate_ids
    expected = [str(candidate.get("candidate_id") or "") for candidate in candidates]
    if len(covered) != len(set(covered)) or set(covered) != set(expected):
        raise ValueError("candidate relation plan must provide exact coverage")
    return {
        "relations": relations,
        "buckets": buckets,
        "singleton_candidate_ids": singleton_candidate_ids,
        "stats": {
            "candidates": len(candidates),
            "relations": len(relations),
            "buckets": len(buckets),
            "singletons": len(singleton_candidate_ids),
        },
    }


def build_insight_editor_prompt(bucket: dict[str, Any]) -> str:
    """Build a strict topic-level report editing prompt."""
    compact_items = [_compact_candidate(item) for item in bucket.get("items") or []]
    return "\n".join([
        "You edit daily product insights from atomic WeType feedback topics.",
        "Return exactly one legal JSON object and cover every supplied candidate_id exactly once.",
        "Atomic topics answer what users reported; report insights answer what deserves proactive product attention today.",
        "Report grouping must not modify topic memory, stable topic IDs, members, or lifecycle events.",
        "A lifecycle verdict of new_topic does not prove a product-level new issue.",
        "Do not merge merely because candidates share a feature, platform, sentiment, or broad feedback type.",
        "Merge candidates only when one report explanation can preserve their actionable product meaning without hiding different fixes or decisions.",
        "Bug reports and requests remain separate unless they clearly express one capability gap and grouping does not hide their nature.",
        "Use main only for evidence that genuinely deserves proactive attention. Use observe for useful but not main-report evidence.",
        "Use manual_review when evidence or grouping remains ambiguous. Use exclude for low-value or non-reportable candidates.",
        "A concrete single feedback may be high value, but strong wording alone does not prove importance or severity.",
        "Use only a trend_claim allowed by every grouped candidate. Never claim rising when rising is not allowed.",
        "Do not infer image or video contents, population impact, fix priority, root cause, or facts absent from the evidence.",
        "Allowed report_decision values: main | observe | exclude | manual_review.",
        "Allowed signal_type values: new_bug | rising_or_repeated_bug | demand_opportunity | high_value_single | not_reportable.",
        "Keep headline under 28 Chinese characters, summary under 120 Chinese characters, and selection_reason under 100 Chinese characters.",
        "Use only supplied candidate_id and issue_unit_id values.",
        "Do not use literal ASCII double quotes inside free-text fields; use Chinese corner quotes when needed.",
        "",
        f"Editor bucket: {bucket.get('bucket_id')}",
        "```json",
        json.dumps(compact_items, ensure_ascii=False, sort_keys=True),
        "```",
        "",
        "Return shape:",
        json.dumps({
            "insights": [{
                "report_decision": "main | observe | exclude | manual_review",
                "signal_type": "new_bug | rising_or_repeated_bug | demand_opportunity | high_value_single | not_reportable",
                "headline": "concise factual title",
                "summary": "what happened and why it matters",
                "selection_reason": "why this belongs in this decision layer",
                "trend_claim": "none | new_signal | repeated | persistent | reappeared | rising",
                "source_candidate_ids": ["supplied candidate id"],
                "representative_issue_unit_ids": ["supplied issue unit id"],
                "confidence": 0.0,
                "needs_human_review": False,
            }]
        }, ensure_ascii=False),
    ])


def parse_insight_editor_reply(
    reply: str,
    *,
    allowed_candidate_ids: set[str],
    allowed_trend_claims: dict[str, set[str]],
    allowed_issue_unit_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    raw = _extract_json(reply)
    if raw is None:
        raise ValueError("no JSON found in insight editor reply")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid insight editor JSON: {exc}") from exc
    values = payload.get("insights") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError("insights must be a list")

    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("insight must be an object")
        decision = str(value.get("report_decision") or "")
        signal_type = str(value.get("signal_type") or "")
        trend_claim = str(value.get("trend_claim") or "")
        if decision not in _REPORT_DECISIONS:
            raise ValueError(f"unsupported report decision: {decision}")
        if signal_type not in _SIGNAL_TYPES:
            raise ValueError(f"unsupported signal type: {signal_type}")
        if trend_claim not in _TREND_CLAIMS:
            raise ValueError(f"unsupported trend claim: {trend_claim}")
        headline = str(value.get("headline") or "").strip()
        if not headline:
            raise ValueError("insight headline is required")
        source_candidate_ids = _dedupe_strings(value.get("source_candidate_ids") or [])
        if not source_candidate_ids or any(
            candidate_id not in allowed_candidate_ids for candidate_id in source_candidate_ids
        ):
            raise ValueError("insight contains unknown or empty candidate IDs")
        issue_unit_ids = _dedupe_strings(value.get("representative_issue_unit_ids") or [])
        if allowed_issue_unit_ids is not None and any(
            issue_unit_id not in allowed_issue_unit_ids for issue_unit_id in issue_unit_ids
        ):
            raise ValueError("insight contains unknown issue unit IDs")
        legal_claims = set(allowed_trend_claims[source_candidate_ids[0]])
        for candidate_id in source_candidate_ids[1:]:
            legal_claims.intersection_update(allowed_trend_claims[candidate_id])
        if trend_claim not in legal_claims:
            raise ValueError("unsupported trend claim")
        rows.append({
            "report_decision": decision,
            "signal_type": signal_type,
            "headline": headline[:120],
            "summary": str(value.get("summary") or "").strip()[:500],
            "selection_reason": str(value.get("selection_reason") or "").strip()[:400],
            "trend_claim": trend_claim,
            "source_candidate_ids": source_candidate_ids,
            "representative_issue_unit_ids": issue_unit_ids,
            "confidence": _clamp(value.get("confidence")),
            "needs_human_review": bool(value.get("needs_human_review")) or decision == "manual_review",
        })

    covered = [candidate_id for row in rows for candidate_id in row["source_candidate_ids"]]
    if len(covered) != len(set(covered)) or set(covered) != allowed_candidate_ids:
        raise ValueError("insight candidates must provide exact coverage")
    return rows


def run_insight_editor_multi_channel(
    buckets: list[dict[str, Any]],
    *,
    routes: list[Any],
    output_path: str | Path,
    concurrency_per_route: int = 4,
    max_retries: int = 4,
    request_timeout: int = 300,
    resume: bool = False,
    call_fn: Callable[..., Any] = call_model_route,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run strict editor buckets through the shared pauseable route scheduler."""
    if request_timeout < 1:
        raise ValueError("request_timeout must be positive")
    normalized_routes = normalize_model_routes(routes)
    jobs = [
        (index, str(bucket.get("bucket_id") or ""), bucket)
        for index, bucket in enumerate(buckets)
    ]

    def worker(index: int, key: str, bucket: dict[str, Any], route: ModelRoute):
        del index
        raw_replies: list[str] = []
        model_attempts = 0
        parse_attempts = 0
        retry_chain: list[str] = []
        elapsed_ms = 0

        def call_and_parse(target: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
            nonlocal model_attempts, parse_attempts, elapsed_ms
            items = list(target.get("items") or [])
            allowed_candidates = {str(item.get("candidate_id") or "") for item in items}
            allowed_trends = {
                str(item.get("candidate_id") or ""): set(item.get("allowed_trend_claims") or ["none"])
                for item in items
            }
            allowed_issues = {
                str(unit.get("issue_unit_id") or "")
                for item in items
                for unit in item.get("representative_issue_units") or []
                if unit.get("issue_unit_id")
            }
            last_error = None
            for _attempt in range(2):
                reply = invoke_model_route(
                    build_insight_editor_prompt(target),
                    route=route,
                    call_fn=call_fn,
                    max_retries=max_retries,
                    timeout=request_timeout,
                )
                raw_replies.append(reply.content)
                model_attempts += reply.attempts
                parse_attempts += 1
                elapsed_ms += reply.elapsed_ms
                retry_chain.extend(reply.retry_chain)
                try:
                    decisions = parse_insight_editor_reply(
                        reply.content,
                        allowed_candidate_ids=allowed_candidates,
                        allowed_trend_claims=allowed_trends,
                        allowed_issue_unit_ids=allowed_issues,
                    )
                    _validate_main_links(decisions, items)
                    return decisions, None
                except ValueError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
            return [], last_error

        split_after_parse_failure = False
        try:
            decisions, parse_error = call_and_parse(bucket)
            if parse_error and len(bucket.get("items") or []) > 3:
                split_after_parse_failure = True
                decisions = []
                split_errors = []
                items = list(bucket.get("items") or [])
                for split_index, start in enumerate(range(0, len(items), 3), 1):
                    split_bucket = {
                        "bucket_id": f"{key}:split:{split_index}",
                        "items": items[start:start + 3],
                    }
                    split_decisions, split_error = call_and_parse(split_bucket)
                    decisions.extend(split_decisions)
                    if split_error:
                        split_errors.append(split_error)
                parse_error = "; ".join(split_errors) or None
            call_error = None
        except QuotaExhaustedError:
            raise
        except Exception as exc:
            decisions = []
            call_error = f"{type(exc).__name__}: {exc}"
            parse_error = None

        for decision_index, decision in enumerate(decisions, 1):
            decision["insight_id"] = f"insight:{key}:{decision_index:02d}"
        row = {
            **bucket,
            "insights": decisions,
            "editor_raw_reply": raw_replies[-1] if raw_replies else "",
            "editor_error": call_error,
            "editor_parse_error": parse_error,
            "parse_status": "ok" if call_error is None and parse_error is None else "failed",
            "editor_parse_attempts": parse_attempts,
            "editor_split_after_parse_failure": split_after_parse_failure,
            "route_source": route.name,
            "endpoint_class": route.endpoint_class,
            "model": route.model or "agent_default",
            "prompt_version": "daily_insight_editor_v1",
            "attempts": model_attempts,
            "retry_chain": retry_chain,
            "elapsed_ms": elapsed_ms,
        }
        return row, call_error or parse_error

    rows, scheduler_stats = run_pauseable_model_jobs(
        jobs,
        worker,
        routes=normalized_routes,
        output_path=output_path,
        concurrency_per_route=concurrency_per_route,
        resume=resume,
    )
    return rows, {
        "batches": len(rows),
        "decisions": sum(len(row.get("insights") or []) for row in rows),
        "call_failed": sum(bool(row.get("editor_error")) for row in rows),
        "parse_failed": sum(bool(row.get("editor_parse_error")) for row in rows),
        "route_counts": dict(scheduler_stats["route_counts"]),
        "processed": scheduler_stats["processed"],
        "resumed": scheduler_stats["resumed"],
        "retryable_failed": scheduler_stats["failed"],
        "run_status": scheduler_stats["run_status"],
        "remaining": scheduler_stats["remaining"],
        **({
            "quota_route": scheduler_stats["quota_route"],
            "quota_status_code": scheduler_stats["quota_status_code"],
        } if scheduler_stats["run_status"] == "paused_quota_exhausted" else {}),
    }


def _validate_candidate_embeddings(
    candidates: list[dict[str, Any]],
    daily_topic_ids: list[str],
    daily_embeddings: np.ndarray,
) -> None:
    if daily_embeddings.ndim != 2 or daily_embeddings.shape[0] != len(daily_topic_ids):
        raise ValueError("daily embedding rows must match daily_topic_ids")
    if len(daily_topic_ids) != len(set(daily_topic_ids)):
        raise ValueError("duplicate daily_topic_id in embeddings")
    if not np.isfinite(daily_embeddings).all():
        raise ValueError("daily embeddings must be finite")
    norms = np.linalg.norm(daily_embeddings.astype("float64"), axis=1)
    if len(norms) and not np.allclose(norms, 1.0, atol=1e-3, rtol=1e-3):
        raise ValueError("daily embeddings must be unit-normalized")
    candidate_ids = [str(candidate.get("candidate_id") or "") for candidate in candidates]
    if not all(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_id must be present and unique")
    candidate_daily_ids = [str(candidate.get("daily_topic_id") or "") for candidate in candidates]
    if not all(candidate_daily_ids) or len(candidate_daily_ids) != len(set(candidate_daily_ids)):
        raise ValueError("candidate daily_topic_id must be present and unique")
    unknown = sorted(set(candidate_daily_ids) - set(daily_topic_ids))
    if unknown:
        raise ValueError(f"candidate has unknown daily topic embedding: {', '.join(unknown)}")


def _candidate_vectors(
    candidates: list[dict[str, Any]],
    daily_topic_ids: list[str],
    daily_embeddings: np.ndarray,
) -> np.ndarray:
    index_by_id = {topic_id: index for index, topic_id in enumerate(daily_topic_ids)}
    indexes = [index_by_id[str(candidate["daily_topic_id"])] for candidate in candidates]
    if not indexes:
        return np.empty((0, daily_embeddings.shape[1]), dtype="float32")
    return daily_embeddings[indexes].astype("float32", copy=True)


def _structural_relations(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for left, right in combinations(candidates, 2):
        left_stable = str(left.get("stable_topic_id") or "")
        right_stable = str(right.get("stable_topic_id") or "")
        left_parents = set(str(value) for value in left.get("parent_topic_ids") or [])
        right_parents = set(str(value) for value in right.get("parent_topic_ids") or [])
        if left_stable and left_stable == right_stable:
            relation_type = "same_stable_topic"
        elif (left_stable and left_stable in right_parents) or (
            right_stable and right_stable in left_parents
        ):
            relation_type = "parent_subtopic"
        else:
            continue
        relations.append(_relation(left, right, relation_type, None))
    return relations


def _semantic_relations(
    candidates: list[dict[str, Any]],
    vectors: np.ndarray,
    *,
    threshold: float,
    top_k: int,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    similarities = np.einsum(
        "ik,jk->ij",
        vectors.astype("float64"),
        vectors.astype("float64"),
        dtype=np.float64,
    )
    pairs: set[tuple[int, int]] = set()
    for left in range(len(candidates)):
        ranked = sorted(
            (
                (float(similarities[left, right]), str(candidates[right]["candidate_id"]), right)
                for right in range(len(candidates))
                if right != left and float(similarities[left, right]) >= threshold
            ),
            key=lambda value: (-value[0], value[1]),
        )[:top_k]
        for _score, _candidate_id, right in ranked:
            pairs.add((min(left, right), max(left, right)))
    return [
        _relation(
            candidates[left],
            candidates[right],
            "semantic_similarity",
            float(similarities[left, right]),
        )
        for left, right in sorted(pairs)
    ]


def _relation(
    left: dict[str, Any],
    right: dict[str, Any],
    relation_type: str,
    similarity: float | None,
) -> dict[str, Any]:
    left_id, right_id = sorted([
        str(left.get("candidate_id") or ""),
        str(right.get("candidate_id") or ""),
    ])
    return {
        "left_candidate_id": left_id,
        "right_candidate_id": right_id,
        "relation_type": relation_type,
        "cosine_similarity": similarity,
    }


def _dedupe_relations(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for relation in sorted(
        relations,
        key=lambda row: (
            _RELATION_PRIORITY[str(row["relation_type"])],
            str(row["left_candidate_id"]),
            str(row["right_candidate_id"]),
        ),
    ):
        key = (str(relation["left_candidate_id"]), str(relation["right_candidate_id"]))
        selected.setdefault(key, relation)
    return sorted(
        selected.values(),
        key=lambda row: (
            _RELATION_PRIORITY[str(row["relation_type"])],
            str(row["left_candidate_id"]),
            str(row["right_candidate_id"]),
        ),
    )


def _bounded_components(
    candidates: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    *,
    max_bucket_size: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    ids = [str(candidate["candidate_id"]) for candidate in candidates]
    index_by_id = {candidate_id: index for index, candidate_id in enumerate(ids)}
    parents = list(range(len(candidates)))
    sizes = [1] * len(candidates)

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for relation in relations:
        left = find(index_by_id[str(relation["left_candidate_id"])])
        right = find(index_by_id[str(relation["right_candidate_id"])])
        if left == right or sizes[left] + sizes[right] > max_bucket_size:
            continue
        if ids[left] > ids[right]:
            left, right = right, left
        parents[right] = left
        sizes[left] += sizes[right]

    components: dict[int, list[int]] = {}
    for index in range(len(candidates)):
        components.setdefault(find(index), []).append(index)
    ordered = sorted(
        (sorted(indexes, key=lambda index: ids[index]) for indexes in components.values()),
        key=lambda indexes: ids[indexes[0]],
    )
    buckets = [
        {
            "bucket_id": f"insight-rel:{bucket_index:04d}",
            "items": [candidates[index] for index in indexes],
        }
        for bucket_index, indexes in enumerate((value for value in ordered if len(value) > 1), 1)
    ]
    singletons = [ids[indexes[0]] for indexes in ordered if len(indexes) == 1]
    return buckets, singletons


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "daily_topic_id": candidate.get("daily_topic_id"),
        "stable_topic_id": candidate.get("stable_topic_id"),
        "parent_topic_ids": candidate.get("parent_topic_ids") or [],
        "title": candidate.get("title"),
        "description": candidate.get("description"),
        "today_conversation_count": candidate.get("today_conversation_count"),
        "baseline_dates": candidate.get("baseline_dates") or [],
        "baseline_daily_counts": candidate.get("baseline_daily_counts") or [],
        "historical_active_dates": candidate.get("historical_active_dates") or [],
        "lifecycle_verdict": candidate.get("lifecycle_verdict"),
        "feedback_type_counts": candidate.get("feedback_type_counts") or {},
        "feature_candidate_counts": candidate.get("feature_candidate_counts") or {},
        "platform_counts": candidate.get("platform_counts") or {},
        "appversion_counts": candidate.get("appversion_counts") or {},
        "recall_reasons": candidate.get("recall_reasons") or [],
        "candidate_band": candidate.get("candidate_band"),
        "allowed_trend_claims": candidate.get("allowed_trend_claims") or ["none"],
        "feature_policy": candidate.get("feature_policy") or {},
        "has_media_evidence": bool(candidate.get("has_media_evidence")),
        "needs_review": bool(candidate.get("needs_review")),
        "source_links": candidate.get("source_links") or [],
        "representative_issue_units": candidate.get("representative_issue_units") or [],
    }


def _validate_main_links(
    insights: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> None:
    candidate_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in candidates
    }
    for insight in insights:
        if insight["report_decision"] != "main":
            continue
        links = {
            str(link).strip()
            for candidate_id in insight["source_candidate_ids"]
            for link in candidate_by_id[candidate_id].get("source_links") or []
            if str(link).strip()
        }
        if not links:
            raise ValueError("main insight requires a source link")


def _extract_json(reply: str) -> str | None:
    fenced = _FENCE_JSON_RE.search(reply)
    if fenced:
        return fenced.group(1).strip()
    braced = _BRACE_RE.search(reply)
    return braced.group(0).strip() if braced else None


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
