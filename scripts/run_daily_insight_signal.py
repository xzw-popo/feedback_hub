#!/usr/bin/env python3
"""Run the offline daily insight signal experiment."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from feedback_hub.topic_discovery.daily_insight_run import (
    DailyInsightConfig,
    run_daily_insight_experiment,
)


def build_routes(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Build model routes from endpoint arguments and named environment variables."""
    routes: list[dict[str, Any]] = []
    for name, api_url, token_env in (
        ("agent_a", args.route_a_url, args.route_a_token_env),
        ("agent_c", args.route_c_url, args.route_c_token_env),
    ):
        credential = os.getenv(token_env, "")
        if api_url and credential:
            routes.append({
                "name": name,
                "endpoint_class": "knot_agent",
                "api_url": api_url,
                "token": credential,
            })
    openai_credential = os.getenv(args.openai_key_env, "")
    if args.openai_url and args.openai_model and openai_credential:
        routes.append({
            "name": "openai_primary",
            "endpoint_class": "openai_compatible",
            "api_url": args.openai_url,
            "token": openai_credential,
            "model": args.openai_model,
        })
    return routes


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a resumable daily insight signal experiment.")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--baseline-days", type=int, default=7)
    parser.add_argument("--route-a-url", default="")
    parser.add_argument("--route-a-token-env", default="KNOT_API_TOKEN_A")
    parser.add_argument("--route-c-url", default="")
    parser.add_argument("--route-c-token-env", default="KNOT_API_TOKEN_C")
    parser.add_argument("--openai-url", default="")
    parser.add_argument("--openai-key-env", default="GLM_API_KEY")
    parser.add_argument("--openai-model", default="")
    parser.add_argument("--concurrency-per-route", type=int, default=4)
    parser.add_argument("--relation-threshold", type=float, default=0.78)
    parser.add_argument("--relation-top-k", type=int, default=5)
    parser.add_argument("--relation-max-bucket-size", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    routes = build_routes(args)
    if not routes:
        raise SystemExit("no model route credential is configured")
    config = DailyInsightConfig(
        run_root=Path(args.run_root),
        report_date=date.fromisoformat(args.report_date),
        baseline_days=args.baseline_days,
        catalog_path=Path(args.catalog),
        output_dir=Path(args.output_dir),
        routes=tuple(routes),
        concurrency_per_route=args.concurrency_per_route,
        relation_threshold=args.relation_threshold,
        relation_top_k=args.relation_top_k,
        relation_max_bucket_size=args.relation_max_bucket_size,
        resume=args.resume,
    )
    summary = run_daily_insight_experiment(config)
    print(json.dumps({
        "run_status": summary["run_status"],
        "report_date": summary["report_date"],
        "feature_topics": summary["feature_topics"],
        "candidate_topics": summary["candidate_topics"],
        "main_insights": summary.get("main_insights"),
        "observe_insights": summary.get("observe_insights"),
        "manual_review_insights": summary.get("manual_review_insights"),
        "route_counts": summary.get("route_counts") or {},
        "output_dir": str(config.output_dir),
    }, ensure_ascii=False))
    if summary["run_status"] == "paused_quota_exhausted":
        return 75
    return 0 if summary["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
