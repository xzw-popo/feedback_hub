#!/usr/bin/env python3
"""Rerun risky cross-day topic decisions with refined memory boundaries."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from feedback_hub.topic_discovery.lifecycle import (
    apply_topic_events,
    match_daily_topics,
    seed_topic_store,
)
from feedback_hub.topic_discovery.lifecycle_matching import (
    run_lifecycle_batches_multi_channel,
)
from feedback_hub.topic_discovery.refinement import (
    audit_refined_decisions,
    build_low_information_pool,
    overlay_revised_decisions,
    select_targeted_topics,
)


DEFAULT_ROUTE_A_URL = "http://knot.woa.com/apigw/api/v1/agents/agui/e19c3ca2c99f41ed970bedd4192e45e9"
DEFAULT_ROUTE_B_URL = "http://knot.woa.com/apigw/api/v1/agents/agui/7f04d96e3ae34ad0a73b912785011e35"


def build_artifact_paths(output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    return {
        "targeted": root / "targeted_topics.jsonl",
        "batches": root / "revised_lifecycle_batches.jsonl",
        "model_rows": root / "revised_lifecycle_model_rows.jsonl",
        "revised": root / "revised_decisions.jsonl",
        "full": root / "full_overlaid_decisions.jsonl",
        "pool": root / "low_information_pool.jsonl",
        "audit": root / "boundary_audit.jsonl",
        "events": root / "topic_events.jsonl",
        "store": root / "topic_store.jsonl",
        "summary": root / "comparison_summary.json",
        "manifest": root / "manifest.json",
    }


def build_refinement_batches(
    targeted_rows: list[dict[str, Any]],
    historical_daily_topics: list[dict[str, Any]],
    *,
    max_batch_size: int = 10,
) -> list[dict[str, Any]]:
    if max_batch_size < 1:
        raise ValueError("max_batch_size must be positive")
    target_ids = [str(row.get("daily_topic_id") or "") for row in targeted_rows]
    if any(not value for value in target_ids):
        raise ValueError("target rows require daily_topic_id")
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("duplicate targeted daily_topic_id")

    history_by_topic_id = {
        f"topic:{index:06d}": topic
        for index, topic in enumerate(
            sorted(
                historical_daily_topics,
                key=lambda value: str(value.get("daily_topic_id") or ""),
            ),
            1,
        )
    }
    items = []
    for row in sorted(targeted_rows, key=lambda value: str(value.get("daily_topic_id") or "")):
        daily_topic_id = str(row.get("daily_topic_id") or "")
        current = row.get("daily_topic") or {}
        if str(current.get("daily_topic_id") or "") != daily_topic_id:
            raise ValueError(f"target daily topic mismatch: {daily_topic_id}")
        candidates = []
        for candidate in row.get("candidates") or []:
            topic_id = str(candidate.get("topic_id") or "")
            historical = history_by_topic_id.get(topic_id)
            if historical is None:
                raise ValueError(f"unknown historical candidate: {topic_id}")
            facts = _topic_facts(historical)
            candidates.append({
                "topic_id": topic_id,
                "source_daily_topic_id": str(historical.get("daily_topic_id") or ""),
                "canonical_title": str(
                    candidate.get("canonical_title") or historical.get("title") or ""
                ),
                "canonical_description": str(
                    candidate.get("canonical_description") or historical.get("description") or ""
                ),
                "cosine_similarity": float(candidate.get("cosine_similarity") or 0.0),
                **facts,
            })
        items.append({
            "daily_topic_id": daily_topic_id,
            "daily_topic": _topic_facts(current, include_id=True),
            "candidates": candidates,
            "selection_reasons": list(row.get("selection_reasons") or []),
        })
    return [
        {
            "batch_id": f"refine:{start // max_batch_size + 1:04d}",
            "items": items[start:start + max_batch_size],
        }
        for start in range(0, len(items), max_batch_size)
    ]


def summarize_refinement(
    targeted_rows: list[dict[str, Any]],
    baseline_decisions: list[dict[str, Any]],
    revised_decisions: list[dict[str, Any]],
    full_decisions: list[dict[str, Any]],
    low_information_pool: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    model_stats: dict[str, Any],
) -> dict[str, Any]:
    target_ids = {str(row.get("daily_topic_id") or "") for row in targeted_rows}
    baseline_by_id = {
        str(row.get("daily_topic_id") or ""): row
        for row in baseline_decisions
        if str(row.get("daily_topic_id") or "") in target_ids
    }
    revised_by_id = {
        str(row.get("daily_topic_id") or ""): row for row in revised_decisions
    }
    if set(baseline_by_id) != target_ids or set(revised_by_id) != target_ids:
        raise ValueError("summary requires exact targeted decision coverage")

    def boundary(decision: dict[str, Any]) -> tuple[str, str]:
        return (
            str(decision.get("verdict") or ""),
            str(decision.get("historical_topic_id") or ""),
        )

    selection_reasons = Counter(
        str(reason)
        for row in targeted_rows
        for reason in row.get("selection_reasons") or []
    )
    audit_flags = Counter(
        str(flag)
        for row in audit_rows
        for flag in row.get("audit_flags") or []
    )
    return {
        "targeted_topics": len(target_ids),
        "selection_reason_counts": dict(sorted(selection_reasons.items())),
        "old_verdict_counts": dict(sorted(Counter(
            str(row.get("verdict") or "") for row in baseline_by_id.values()
        ).items())),
        "new_verdict_counts": dict(sorted(Counter(
            str(row.get("verdict") or "") for row in revised_by_id.values()
        ).items())),
        "changed_decisions": sum(
            boundary(baseline_by_id[daily_id]) != boundary(revised_by_id[daily_id])
            for daily_id in target_ids
        ),
        "full_overlaid_decisions": len(full_decisions),
        "full_verdict_counts": dict(sorted(Counter(
            str(row.get("verdict") or "") for row in full_decisions
        ).items())),
        "low_information_pool": len(low_information_pool),
        "low_information_media": sum(
            bool(row.get("has_media_evidence")) for row in low_information_pool
        ),
        "audited_topics": len(audit_rows),
        "audit_flag_counts": dict(sorted(audit_flags.items())),
        "model": deepcopy(model_stats),
    }


def _topic_facts(topic: dict[str, Any], *, include_id: bool = False) -> dict[str, Any]:
    members = [value for value in topic.get("members") or [] if isinstance(value, dict)]
    summaries = _dedupe([
        str(member.get("summary") or "") for member in members
    ])[:5]
    evidence_spans = _dedupe([
        str(span)
        for member in members
        for span in member.get("evidence_spans") or []
    ])[:8]
    evidence_links = _dedupe([
        *[str(value) for value in topic.get("evidence_links") or []],
        *[
            str(link)
            for member in members
            for link in member.get("evidence_links") or []
        ],
    ])[:5]
    facts = {
        "title": str(topic.get("title") or ""),
        "description": str(topic.get("description") or ""),
        "issue_unit_count": int(topic.get("issue_unit_count") or len(members)),
        "conversation_count": int(topic.get("conversation_count") or 0),
        "feature_candidate_counts": dict(topic.get("feature_candidate_counts") or {}),
        "feedback_type_counts": dict(topic.get("feedback_type_counts") or {}),
        "platform_counts": dict(topic.get("platform_counts") or {}),
        "representative_summaries": summaries,
        "evidence_spans": evidence_spans,
        "evidence_links": evidence_links,
        "has_media_evidence": bool(topic.get("has_media_evidence")) or any(
            bool(member.get("has_media_evidence")) for member in members
        ),
    }
    if include_id:
        facts["daily_topic_id"] = str(topic.get("daily_topic_id") or "")
        facts["source_date"] = str(topic.get("source_date") or "")
    return facts


def _enriched_historical_topics(
    historical_daily_topics: list[dict[str, Any]],
    source_date: str,
) -> list[dict[str, Any]]:
    ordered = sorted(
        historical_daily_topics,
        key=lambda value: str(value.get("daily_topic_id") or ""),
    )
    store, _events = seed_topic_store(ordered, source_date)
    return [{**deepcopy(daily), **memory} for daily, memory in zip(ordered, store)]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dedupe(values: list[str]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _write_manifest(
    path: Path,
    *,
    input_paths: list[Path],
    artifact_paths: dict[str, Path],
    routes: list[dict[str, str]],
    settings: dict[str, Any],
    stats: dict[str, Any],
    started_at: str,
    finished_at: str,
) -> None:
    code_paths = [
        Path(__file__),
        PROJECT_ROOT / "feedback_hub/topic_discovery/lifecycle.py",
        PROJECT_ROOT / "feedback_hub/topic_discovery/lifecycle_matching.py",
        PROJECT_ROOT / "feedback_hub/topic_discovery/refinement.py",
    ]
    outputs = {
        artifact_path.name: _sha256(artifact_path)
        for key, artifact_path in artifact_paths.items()
        if key != "manifest" and artifact_path.exists()
    }
    _write_json(path, {
        "started_at": started_at,
        "finished_at": finished_at,
        "inputs_sha256": {
            str(input_path): _sha256(input_path) for input_path in input_paths
        },
        "artifacts_sha256": outputs,
        "code_sha256": {str(code_path): _sha256(code_path) for code_path in code_paths},
        "routes": [
            {"name": route["name"], "api_url": route["api_url"]}
            for route in routes
        ],
        "settings": settings,
        "stats": stats,
    })


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rerun targeted topic lifecycle boundaries."
    )
    parser.add_argument("--baseline-lifecycle-dir", required=True)
    parser.add_argument("--historical-daily-dir", required=True)
    parser.add_argument("--current-daily-dir", required=True)
    parser.add_argument("--historical-date", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--route-a-url", default=DEFAULT_ROUTE_A_URL)
    parser.add_argument("--route-a-token-env", default="KNOT_API_TOKEN_A")
    parser.add_argument("--route-b-url", default=DEFAULT_ROUTE_B_URL)
    parser.add_argument("--route-b-token-env", default="KNOT_API_TOKEN_B")
    parser.add_argument("--concurrency-per-route", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--request-timeout", type=int, default=300)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    started_at = datetime.now(timezone.utc).isoformat()
    baseline_dir = Path(args.baseline_lifecycle_dir)
    historical_dir = Path(args.historical_daily_dir)
    current_dir = Path(args.current_daily_dir)
    paths = build_artifact_paths(args.output_dir)
    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)

    baseline_decision_path = baseline_dir / "lifecycle_decisions.jsonl"
    baseline_candidate_path = baseline_dir / "lifecycle_candidates.jsonl"
    historical_topic_path = historical_dir / "daily_topics.jsonl"
    current_topic_path = current_dir / "daily_topics.jsonl"
    input_paths = [
        baseline_decision_path,
        baseline_candidate_path,
        historical_topic_path,
        current_topic_path,
    ]
    baseline_decisions = _read_jsonl(baseline_decision_path)
    baseline_candidates = _read_jsonl(baseline_candidate_path)
    historical_topics = _read_jsonl(historical_topic_path)
    current_topics = _read_jsonl(current_topic_path)
    if any(str(row.get("source_date") or "") != args.historical_date for row in historical_topics):
        raise SystemExit("historical topic source_date mismatch")
    if any(str(row.get("source_date") or "") != args.current_date for row in current_topics):
        raise SystemExit("current topic source_date mismatch")

    targeted = select_targeted_topics(
        current_topics,
        baseline_decisions,
        baseline_candidates,
    )
    batches = build_refinement_batches(
        targeted,
        historical_topics,
        max_batch_size=args.batch_size,
    )
    _write_jsonl(paths["targeted"], targeted)
    _write_jsonl(paths["batches"], batches)

    route_specs = [
        ("agent_a", args.route_a_url, args.route_a_token_env),
        ("agent_b", args.route_b_url, args.route_b_token_env),
    ]
    routes = [
        {"name": name, "api_url": api_url, "token": token}
        for name, api_url, token_env in route_specs
        if (token := os.getenv(token_env, ""))
    ]
    settings = {
        "historical_date": args.historical_date,
        "current_date": args.current_date,
        "batch_size": args.batch_size,
        "concurrency_per_route": args.concurrency_per_route,
        "max_retries": args.max_retries,
        "request_timeout": args.request_timeout,
        "resume": args.resume,
        "prepare_only": args.prepare_only,
    }
    if args.prepare_only:
        preparation = {
            "mode": "prepare_only",
            "targeted_topics": len(targeted),
            "batches": len(batches),
            "selection_reason_counts": dict(sorted(Counter(
                str(reason)
                for row in targeted
                for reason in row.get("selection_reasons") or []
            ).items())),
            "old_verdict_counts": dict(sorted(Counter(
                str(row.get("baseline_decision", {}).get("verdict") or "")
                for row in targeted
            ).items())),
        }
        _write_json(paths["summary"], preparation)
        _write_manifest(
            paths["manifest"],
            input_paths=input_paths,
            artifact_paths=paths,
            routes=routes,
            settings=settings,
            stats=preparation,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        print(json.dumps(preparation, ensure_ascii=False, sort_keys=True))
        return 0

    if batches and not routes:
        raise SystemExit("no Knot route token is configured")
    model_rows, model_stats = run_lifecycle_batches_multi_channel(
        batches,
        routes=routes,
        output_path=paths["model_rows"],
        concurrency_per_route=args.concurrency_per_route,
        max_retries=args.max_retries,
        request_timeout=args.request_timeout,
        resume=args.resume,
    )
    if model_stats["call_failed"] or model_stats["parse_failed"]:
        raise RuntimeError(f"unresolved lifecycle model rows: {model_stats}")

    revised_decisions = [
        {**decision, "decision_source": "targeted_refinement"}
        for row in model_rows
        for decision in row.get("decisions") or []
    ]
    target_ids = [str(row.get("daily_topic_id") or "") for row in targeted]
    revised_ids = [str(row.get("daily_topic_id") or "") for row in revised_decisions]
    if len(revised_ids) != len(set(revised_ids)) or set(revised_ids) != set(target_ids):
        raise RuntimeError(
            "invalid revised decision coverage: "
            f"targets={len(target_ids)}, decisions={len(revised_ids)}, unique={len(set(revised_ids))}"
        )
    revised_decisions.sort(key=lambda row: str(row.get("daily_topic_id") or ""))
    full_decisions = overlay_revised_decisions(
        [str(row.get("daily_topic_id") or "") for row in current_topics],
        baseline_decisions,
        revised_decisions,
    )
    enriched_history = _enriched_historical_topics(historical_topics, args.historical_date)
    events = match_daily_topics(current_topics, enriched_history, decisions=full_decisions)
    topic_store = apply_topic_events(enriched_history, current_topics, events)
    pool = build_low_information_pool(current_topics, full_decisions)
    audit = audit_refined_decisions(
        current_topics,
        enriched_history,
        full_decisions,
        baseline_candidates,
    )
    summary = summarize_refinement(
        targeted,
        baseline_decisions,
        revised_decisions,
        full_decisions,
        pool,
        audit,
        model_stats,
    )
    summary["lifecycle_event_counts"] = dict(sorted(Counter(
        str(row.get("event_type") or "") for row in events
    ).items()))
    summary["stable_topic_store"] = len(topic_store)

    for key, rows in (
        ("revised", revised_decisions),
        ("full", full_decisions),
        ("pool", pool),
        ("audit", audit),
        ("events", events),
        ("store", topic_store),
    ):
        _write_jsonl(paths[key], rows)
    _write_json(paths["summary"], summary)
    _write_manifest(
        paths["manifest"],
        input_paths=input_paths,
        artifact_paths=paths,
        routes=routes,
        settings=settings,
        stats=summary,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
