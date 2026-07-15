"""Embedding recall and strict model decisions for cross-day topic matching."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
import threading
import time
from typing import Any, Callable

import numpy as np

from feedback_hub.data.knot_label_eval.knot_batch import run_incremental_jobs
from feedback_hub.data.knot_label_eval.run_knot_feature_canary import call_knot


_FENCE_JSON_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_BRACE_RE = re.compile(r"\{[\s\S]*\}")
_VERDICTS = {
    "same_topic",
    "new_topic",
    "possible_subtopic",
    "uncertain",
    "low_information",
}


def aggregate_topic_embeddings(
    topics: list[dict[str, Any]],
    issue_unit_ids: list[str],
    issue_embeddings: np.ndarray,
) -> np.ndarray:
    if issue_embeddings.ndim != 2 or issue_embeddings.shape[0] != len(issue_unit_ids):
        raise ValueError("issue embedding rows must match issue_unit_ids")
    if len(set(issue_unit_ids)) != len(issue_unit_ids):
        raise ValueError("duplicate issue_unit_id")
    if not np.isfinite(issue_embeddings).all():
        raise ValueError("issue embeddings must be finite")
    index_by_id = {str(issue_id): index for index, issue_id in enumerate(issue_unit_ids)}
    vectors = []
    for topic in topics:
        member_ids = [str(value) for value in topic.get("member_issue_unit_ids") or []]
        if not member_ids or any(issue_id not in index_by_id for issue_id in member_ids):
            raise ValueError("topic contains missing or unknown issue members")
        mean = issue_embeddings[[index_by_id[issue_id] for issue_id in member_ids]].astype("float64").mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if not np.isfinite(norm) or norm <= 1e-12:
            raise ValueError("topic member mean cannot be normalized")
        vectors.append((mean / norm).astype("float32"))
    if not vectors:
        return np.empty((0, issue_embeddings.shape[1]), dtype="float32")
    return np.vstack(vectors)


def build_lifecycle_candidate_plan(
    daily_topics: list[dict[str, Any]],
    historical_topics: list[dict[str, Any]],
    daily_embeddings: np.ndarray,
    historical_embeddings: np.ndarray,
    *,
    threshold: float = 0.55,
    top_k: int = 5,
    max_batch_size: int = 10,
) -> dict[str, Any]:
    if daily_embeddings.shape[0] != len(daily_topics):
        raise ValueError("daily embedding rows must match daily topics")
    if historical_embeddings.shape[0] != len(historical_topics):
        raise ValueError("historical embedding rows must match historical topics")
    if daily_embeddings.ndim != 2 or historical_embeddings.ndim != 2:
        raise ValueError("topic embeddings must be matrices")
    if daily_embeddings.shape[1] != historical_embeddings.shape[1]:
        raise ValueError("topic embedding dimensions must match")
    if not np.isfinite(daily_embeddings).all() or not np.isfinite(historical_embeddings).all():
        raise ValueError("topic embeddings must be finite")
    for name, embeddings in (("daily", daily_embeddings), ("historical", historical_embeddings)):
        norms = np.linalg.norm(embeddings.astype("float64"), axis=1)
        if len(norms) and not np.allclose(norms, 1.0, atol=1e-3, rtol=1e-3):
            raise ValueError(f"{name} embeddings must be unit-normalized")
    if top_k < 1 or max_batch_size < 1:
        raise ValueError("top_k and max_batch_size must be positive")
    similarities = np.einsum(
        "ik,jk->ij",
        daily_embeddings.astype("float64"),
        historical_embeddings.astype("float64"),
        dtype=np.float64,
    )
    candidate_rows = []
    for daily_index, daily_topic in enumerate(daily_topics):
        ranked = sorted(
            (
                (float(similarities[daily_index, historical_index]), historical_index)
                for historical_index in range(len(historical_topics))
                if float(similarities[daily_index, historical_index]) >= threshold
            ),
            key=lambda pair: (-pair[0], str(historical_topics[pair[1]].get("topic_id") or "")),
        )[:top_k]
        candidates = [
            {
                **_compact_historical_topic(historical_topics[historical_index]),
                "cosine_similarity": score,
            }
            for score, historical_index in ranked
        ]
        candidate_rows.append({
            "daily_topic_id": str(daily_topic.get("daily_topic_id") or ""),
            "daily_topic": _compact_daily_topic(daily_topic),
            "candidates": candidates,
        })
    model_candidate_rows = candidate_rows
    batches = [
        {
            "batch_id": f"match:{start // max_batch_size + 1:04d}",
            "items": model_candidate_rows[start:start + max_batch_size],
        }
        for start in range(0, len(model_candidate_rows), max_batch_size)
    ]
    return {
        "candidate_rows": candidate_rows,
        "batches": batches,
        "stats": {
            "daily_topics": len(daily_topics),
            "historical_topics": len(historical_topics),
            "daily_topics_with_candidates": sum(bool(row["candidates"]) for row in candidate_rows),
            "daily_topics_without_candidates": sum(not row["candidates"] for row in candidate_rows),
            "model_decision_topics": len(model_candidate_rows),
            "deterministic_new_topics": 0,
            "candidate_pairs": sum(len(row["candidates"]) for row in candidate_rows),
            "match_batches": len(batches),
        },
    }


def build_lifecycle_match_prompt(batch: dict[str, Any]) -> str:
    return "\n".join([
        "You match newly discovered WeType daily topics against historical topics.",
        "The historical topic store is memory, not a fixed taxonomy. New topics are expected.",
        "Return JSON only and decide every supplied daily_topic_id exactly once.",
        "First test memory eligibility. Use low_information when the text lacks enough product object, request, symptom, trigger, or expectation to define an independently actionable topic.",
        "Low-information feedback is not irrelevant. It is held outside stable topic memory and may still enter a media appendix.",
        "Use same_topic only when the current and historical topics describe the same independently actionable product problem and the same product object or capability.",
        "Shared feature, platform, sentiment, or broad wording alone is not enough. Generic operations such as sorting, sizing, switches, entry points, or 'does not work' do not establish a shared topic boundary.",
        "Bugs are platform- or mechanism-specific by default. Merge bugs across platforms only when the evidence supports one shared investigation and fix.",
        "Feature requests may match across platforms when they can be handled by one product decision and describe the same capability.",
        "Use possible_subtopic when the current topic is a new, independently actionable symptom or variant under the same broader product object or capability.",
        "Use new_topic when the evidence is sufficient to define a topic but no candidate describes the same problem.",
        "When no candidates are supplied, choose new_topic or low_information according to evidence sufficiency.",
        "Use uncertain only when one historical candidate is plausible but the supplied evidence cannot resolve the boundary; provide that candidate ID.",
        "Do not claim rising trend or confirmed novelty from one comparison.",
        "For same_topic, possible_subtopic, and uncertain, historical_topic_id must be one supplied candidate. For new_topic and low_information it must be null.",
        "Keep the reason under 80 Chinese characters.",
        "",
        f"Match batch: {batch.get('batch_id')}",
        "```json",
        json.dumps(batch.get("items") or [], ensure_ascii=False, sort_keys=True),
        "```",
        "",
        "Return shape:",
        "```json",
        json.dumps({
            "decisions": [{
                "daily_topic_id": "daily topic id",
                "verdict": "same_topic | new_topic | possible_subtopic | uncertain | low_information",
                "historical_topic_id": "candidate topic id or null",
                "confidence": 0.0,
                "reason": "concise boundary reason",
            }]
        }, ensure_ascii=False),
        "```",
    ])


def parse_lifecycle_match_reply(
    reply: str,
    *,
    allowed_daily_topic_ids: set[str],
    allowed_historical_topic_ids: set[str],
) -> list[dict[str, Any]]:
    raw = _extract_json(reply)
    if raw is None:
        raise ValueError("no JSON found in lifecycle match reply")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid lifecycle match JSON: {exc}") from exc
    values = obj.get("decisions") if isinstance(obj, dict) else None
    if not isinstance(values, list):
        raise ValueError("lifecycle match decisions must be a list")
    decisions = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("lifecycle match decision must be an object")
        daily_topic_id = str(value.get("daily_topic_id") or "")
        verdict = str(value.get("verdict") or "")
        historical_topic_id = str(value.get("historical_topic_id") or "") or None
        if verdict not in _VERDICTS:
            raise ValueError(f"unsupported lifecycle verdict: {verdict}")
        if daily_topic_id not in allowed_daily_topic_ids:
            raise ValueError(f"unknown daily_topic_id: {daily_topic_id}")
        if verdict == "low_information":
            if historical_topic_id is not None:
                raise ValueError("low_information must not reference a historical topic")
            historical_topic_id = None
        elif verdict == "new_topic":
            historical_topic_id = None
        elif historical_topic_id not in allowed_historical_topic_ids:
            raise ValueError(f"unknown historical_topic_id: {historical_topic_id}")
        decisions.append({
            "daily_topic_id": daily_topic_id,
            "verdict": verdict,
            "historical_topic_id": historical_topic_id,
            "confidence": _clamp(value.get("confidence")),
            "reason": str(value.get("reason") or verdict).strip()[:160],
        })
    actual_ids = [decision["daily_topic_id"] for decision in decisions]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != allowed_daily_topic_ids:
        raise ValueError(
            "invalid lifecycle decision coverage: "
            f"expected={len(allowed_daily_topic_ids)}, actual={len(actual_ids)}, "
            f"unique={len(set(actual_ids))}"
        )
    return decisions


def run_lifecycle_batches_multi_channel(
    batches: list[dict[str, Any]],
    *,
    routes: list[dict[str, str]],
    output_path: str | Path,
    concurrency_per_route: int = 4,
    max_retries: int = 4,
    request_timeout: int = 300,
    resume: bool = False,
    call_fn: Callable[..., str] = call_knot,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not routes:
        raise ValueError("at least one Knot route is required")
    if request_timeout < 1:
        raise ValueError("request_timeout must be positive")
    normalized_routes = []
    names = set()
    for route in routes:
        name = str(route.get("name") or "")
        api_url = str(route.get("api_url") or "")
        token = str(route.get("token") or "")
        if not name or not api_url or not token or name in names:
            raise ValueError("each lifecycle route requires a unique name, api_url, and token")
        names.add(name)
        normalized_routes.append({
            "name": name,
            "api_url": api_url,
            "token": token,
            "api_user": str(route.get("api_user") or ""),
            "model": str(route.get("model") or ""),
        })
    semaphores = {
        route["name"]: threading.BoundedSemaphore(concurrency_per_route)
        for route in normalized_routes
    }
    jobs = [
        (index, str(batch.get("batch_id") or ""), batch)
        for index, batch in enumerate(batches)
    ]

    def worker(index: int, key: str, batch: dict[str, Any]):
        route = normalized_routes[index % len(normalized_routes)]
        allowed_daily_ids = {
            str(item.get("daily_topic_id") or item.get("daily_topic", {}).get("daily_topic_id") or "")
            for item in batch.get("items") or []
        }
        allowed_historical_ids = {
            str(candidate.get("topic_id") or "")
            for item in batch.get("items") or []
            for candidate in item.get("candidates") or []
            if candidate.get("topic_id")
        }
        started = time.time()
        try:
            with semaphores[route["name"]]:
                raw_reply = call_fn(
                    build_lifecycle_match_prompt(batch),
                    api_url=route["api_url"],
                    token=route["token"],
                    model=route["model"],
                    api_user=route["api_user"],
                    max_retries=max_retries,
                    timeout=request_timeout,
                )
            decisions = parse_lifecycle_match_reply(
                raw_reply,
                allowed_daily_topic_ids=allowed_daily_ids,
                allowed_historical_topic_ids=allowed_historical_ids,
            )
            call_error = None
            parse_error = None
        except ValueError as exc:
            raw_reply = locals().get("raw_reply", "")
            decisions = []
            call_error = None
            parse_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            raw_reply = ""
            decisions = []
            call_error = f"{type(exc).__name__}: {exc}"
            parse_error = None
        row = {
            **batch,
            "decisions": decisions,
            "match_raw_reply": raw_reply,
            "match_error": call_error,
            "match_parse_error": parse_error,
            "knot_route": route["name"],
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        return row, call_error or parse_error

    rows, batch_stats = run_incremental_jobs(
        jobs,
        worker,
        output_path=output_path,
        concurrency=len(normalized_routes) * concurrency_per_route,
        resume=resume,
        progress_every=5,
    )
    return rows, {
        "batches": len(rows),
        "decisions": sum(len(row.get("decisions") or []) for row in rows),
        "call_failed": sum(bool(row.get("match_error")) for row in rows),
        "parse_failed": sum(bool(row.get("match_parse_error")) for row in rows),
        "route_counts": dict(sorted(Counter(str(row.get("knot_route") or "") for row in rows).items())),
        "processed": batch_stats["processed"],
        "resumed": batch_stats["resumed"],
        "retryable_failed": batch_stats["failed"],
    }


def _compact_daily_topic(topic: dict[str, Any]) -> dict[str, Any]:
    return {
        "daily_topic_id": str(topic.get("daily_topic_id") or ""),
        "title": str(topic.get("title") or ""),
        "description": str(topic.get("description") or ""),
        "issue_unit_count": int(topic.get("issue_unit_count") or 0),
        "conversation_count": int(topic.get("conversation_count") or 0),
        "platform_counts": dict(topic.get("platform_counts") or {}),
        "representative_summaries": [
            str(member.get("summary") or "")
            for member in (topic.get("members") or [])[:3]
        ],
    }


def _compact_historical_topic(topic: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic_id": str(topic.get("topic_id") or ""),
        "canonical_title": str(topic.get("canonical_title") or ""),
        "canonical_description": str(topic.get("canonical_description") or ""),
        "first_seen": str(topic.get("first_seen") or ""),
        "last_seen": str(topic.get("last_seen") or ""),
        "status": str(topic.get("status") or ""),
        "representative_summaries": [str(value) for value in topic.get("representative_summaries") or []][:3],
    }


def _extract_json(reply: str) -> str | None:
    if not reply:
        return None
    match = _FENCE_JSON_RE.search(reply)
    if match:
        return match.group(1).strip()
    match = _BRACE_RE.search(reply)
    return match.group(0).strip() if match else None


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
