"""Run an offline, resumable daily insight experiment from shadow artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from feedback_hub.tagger.v2.feature_catalog import FeatureCatalog
from feedback_hub.topic_discovery.insight_artifacts import (
    render_daily_report,
    select_main_insights,
    write_review_workbook,
)
from feedback_hub.topic_discovery.insight_editor import (
    build_candidate_relation_plan,
    run_insight_editor_multi_channel,
)
from feedback_hub.topic_discovery.lifecycle_matching import aggregate_topic_embeddings
from feedback_hub.topic_discovery.model_routes import call_model_route
from feedback_hub.topic_discovery.report_signals import (
    build_topic_signal_features,
    recall_report_candidates,
)


@dataclass(frozen=True)
class DailyInsightConfig:
    run_root: Path
    report_date: date
    catalog_path: Path
    output_dir: Path
    routes: tuple[Any, ...]
    baseline_days: int = 7
    concurrency_per_route: int = 4
    relation_threshold: float = 0.78
    relation_top_k: int = 5
    relation_max_bucket_size: int = 8
    resume: bool = False


def run_daily_insight_experiment(
    config: DailyInsightConfig,
    *,
    call_fn: Callable[..., Any] = call_model_route,
) -> dict[str, Any]:
    """Run deterministic recall, model editing, and review artifact rendering."""
    if config.baseline_days < 1:
        raise ValueError("baseline_days must be positive")
    if config.concurrency_per_route < 1:
        raise ValueError("concurrency_per_route must be positive")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = config.output_dir / f"daily_report_draft_{config.report_date.strftime('%Y%m%d')}.md"
    workbook_path = config.output_dir / "daily_insight_review.xlsx"
    if not config.resume:
        report_path.unlink(missing_ok=True)
        workbook_path.unlink(missing_ok=True)

    days = load_shadow_days(config.run_root, config.report_date, config.baseline_days)
    report_day = days[-1]
    catalog = FeatureCatalog.from_xlsx(config.catalog_path)
    features = build_topic_signal_features(
        days,
        report_day["topic_store"],
        catalog,
        report_date=config.report_date.isoformat(),
    )
    _write_jsonl(config.output_dir / "topic_signal_features.jsonl", features)
    candidates = recall_report_candidates(features)
    _write_jsonl(config.output_dir / "candidate_recall.jsonl", candidates)

    eligible_candidates = [row for row in candidates if not row.get("deterministic_exclusion")]
    relation_plan = build_candidate_relation_plan(
        eligible_candidates,
        report_day["daily_topic_ids"],
        report_day["daily_topic_embeddings"],
        threshold=config.relation_threshold,
        top_k=config.relation_top_k,
        max_bucket_size=config.relation_max_bucket_size,
    )
    _write_jsonl(config.output_dir / "candidate_relations.jsonl", relation_plan["relations"])
    editor_buckets = _editor_buckets(eligible_candidates, relation_plan)

    if editor_buckets:
        model_rows, model_stats = run_insight_editor_multi_channel(
            editor_buckets,
            routes=list(config.routes),
            output_path=config.output_dir / "insight_model_rows.jsonl",
            concurrency_per_route=config.concurrency_per_route,
            resume=config.resume,
            call_fn=call_fn,
        )
    else:
        model_rows = []
        model_stats = {
            "batches": 0,
            "decisions": 0,
            "call_failed": 0,
            "parse_failed": 0,
            "route_counts": {},
            "processed": 0,
            "resumed": 0,
            "retryable_failed": 0,
            "run_status": "completed",
            "remaining": 0,
        }

    incomplete = (
        model_stats["run_status"] != "completed"
        or int(model_stats.get("call_failed") or 0) > 0
        or int(model_stats.get("parse_failed") or 0) > 0
        or int(model_stats.get("retryable_failed") or 0) > 0
    )
    if incomplete:
        status = (
            "paused_quota_exhausted"
            if model_stats["run_status"] == "paused_quota_exhausted"
            else "incomplete_model_failures"
        )
        summary = _base_summary(config, features, candidates, relation_plan, model_stats, status)
        _write_json(config.output_dir / "run_summary.json", summary)
        return summary

    insights = [
        insight
        for row in model_rows
        for insight in row.get("insights") or []
    ]
    insights.extend(_deterministic_excluded_insights(candidates))
    _validate_insight_coverage(insights, candidates)
    _write_jsonl(config.output_dir / "insight_decisions.jsonl", insights)

    candidate_by_id = {str(row["candidate_id"]): row for row in candidates}
    selected = select_main_insights(insights, candidate_by_id, max_items=5)
    summary = _base_summary(
        config,
        features,
        candidates,
        relation_plan,
        model_stats,
        "completed",
    )
    summary.update({
        "insight_decisions": len(insights),
        "main_insights": len(selected["main"]),
        "observe_insights": len(selected["observe"]),
        "manual_review_insights": len(selected["manual_review"]),
        "excluded_insights": len(selected["exclude"]),
        "media_appendix": len(report_day["media_appendix"]),
    })
    _write_json(config.output_dir / "run_summary.json", summary)
    report_path.write_text(
        render_daily_report(
            config.report_date.isoformat(),
            selected,
            report_day["media_appendix"],
            summary,
        ),
        encoding="utf-8",
    )
    write_review_workbook(
        workbook_path,
        selected=selected,
        candidates=candidates,
        relations=relation_plan["relations"],
        media_appendix=report_day["media_appendix"],
    )
    return summary


def load_shadow_days(
    run_root: str | Path,
    report_date: date,
    baseline_days: int,
) -> list[dict[str, Any]]:
    """Load exactly the requested baseline and report-day artifacts."""
    run_root = Path(run_root)
    days: list[dict[str, Any]] = []
    for offset in range(baseline_days, -1, -1):
        current = report_date - timedelta(days=offset)
        day_dir = run_root / f"day_{current.strftime('%Y%m%d')}"
        required = [
            day_dir / "daily_topics.jsonl",
            day_dir / "lifecycle_decisions.jsonl",
            day_dir / "issue_units.jsonl",
            day_dir / "media_appendix.jsonl",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError("missing shadow artifacts: " + ", ".join(missing))
        days.append({
            "date": current.isoformat(),
            "daily_topics": _read_jsonl(required[0]),
            "lifecycle_decisions": _read_jsonl(required[1]),
            "issue_units": _read_jsonl(required[2]),
            "media_appendix": _read_jsonl(required[3]),
        })

    report_dir = run_root / f"day_{report_date.strftime('%Y%m%d')}"
    topic_store_path = report_dir / "topic_store.jsonl"
    embedding_path = report_dir / "issue_unit_embeddings.npz"
    if not topic_store_path.exists() or not embedding_path.exists():
        raise FileNotFoundError("report day requires topic_store.jsonl and issue_unit_embeddings.npz")
    report_day = days[-1]
    report_day["topic_store"] = _read_jsonl(topic_store_path)
    with np.load(embedding_path, allow_pickle=False) as payload:
        if "embeddings" not in payload:
            raise ValueError("issue_unit_embeddings.npz must contain embeddings")
        issue_embeddings = payload["embeddings"].astype("float32", copy=True)
    issue_ids = [str(row.get("issue_unit_id") or "") for row in report_day["issue_units"]]
    report_day["daily_topic_ids"] = [
        str(row.get("daily_topic_id") or "") for row in report_day["daily_topics"]
    ]
    report_day["daily_topic_embeddings"] = aggregate_topic_embeddings(
        report_day["daily_topics"],
        issue_ids,
        issue_embeddings,
    )
    return days


def _editor_buckets(
    candidates: list[dict[str, Any]],
    relation_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_by_id = {str(row["candidate_id"]): row for row in candidates}
    buckets = [dict(bucket) for bucket in relation_plan["buckets"]]
    buckets.extend({
        "bucket_id": f"insight-single:{index:04d}",
        "items": [candidate_by_id[candidate_id]],
    } for index, candidate_id in enumerate(relation_plan["singleton_candidate_ids"], 1))
    covered = [
        str(item["candidate_id"])
        for bucket in buckets
        for item in bucket["items"]
    ]
    if len(covered) != len(set(covered)) or set(covered) != set(candidate_by_id):
        raise ValueError("editor buckets must provide exact candidate coverage")
    return buckets


def _deterministic_excluded_insights(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, candidate in enumerate(
        (row for row in candidates if row.get("deterministic_exclusion")),
        1,
    ):
        rows.append({
            "insight_id": f"insight:policy:{index:04d}",
            "report_decision": "exclude",
            "signal_type": "not_reportable",
            "headline": str(candidate.get("title") or "功能政策排除"),
            "summary": str(candidate.get("description") or ""),
            "selection_reason": "功能报告政策明确为不进入报告",
            "trend_claim": "none",
            "source_candidate_ids": [str(candidate["candidate_id"])],
            "representative_issue_unit_ids": [
                str(unit.get("issue_unit_id") or "")
                for unit in candidate.get("representative_issue_units") or []
                if unit.get("issue_unit_id")
            ],
            "confidence": 1.0,
            "needs_human_review": False,
        })
    return rows


def _validate_insight_coverage(
    insights: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> None:
    covered = [
        str(candidate_id)
        for insight in insights
        for candidate_id in insight.get("source_candidate_ids") or []
    ]
    expected = {str(row["candidate_id"]) for row in candidates}
    if len(covered) != len(set(covered)) or set(covered) != expected:
        raise ValueError("insight decisions must provide exact candidate coverage")


def _base_summary(
    config: DailyInsightConfig,
    features: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    relation_plan: dict[str, Any],
    model_stats: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    return {
        "run_status": status,
        "report_date": config.report_date.isoformat(),
        "baseline_start": (config.report_date - timedelta(days=config.baseline_days)).isoformat(),
        "baseline_end": (config.report_date - timedelta(days=1)).isoformat(),
        "feature_topics": len(features),
        "candidate_topics": len(candidates),
        "deterministic_exclusions": sum(bool(row.get("deterministic_exclusion")) for row in candidates),
        "relation_stats": dict(relation_plan["stats"]),
        "model_stats": dict(model_stats),
        "route_counts": dict(model_stats.get("route_counts") or {}),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
