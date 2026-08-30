"""Backend-owned, auditable budgets for topic-mining runs."""

from __future__ import annotations

from .config import TopicMiningConfig
from .contracts import TopicSpec


_DAY_MS = 24 * 60 * 60 * 1000


def effective_days(spec: TopicSpec) -> int:
    """Return elapsed 24-hour units, rounded up with a one-day minimum."""
    elapsed_ms = max(
        1,
        int((spec.scope.end_time - spec.scope.start_time).total_seconds() * 1000),
    )
    return max(1, (elapsed_ms + _DAY_MS - 1) // _DAY_MS)


def candidate_budget(
    spec: TopicSpec, config: TopicMiningConfig,
) -> dict[str, int | str]:
    """Return the immutable classification budget for a new Run."""
    days = effective_days(spec)
    if spec.mode == "exhaustive":
        return {
            "mode": "exhaustive",
            "effective_days": days,
            "maximum": config.exhaustive_candidate_limit,
            "effective_limit": config.exhaustive_candidate_limit,
        }
    return {
        "mode": "standard",
        "effective_days": days,
        "minimum": config.standard_candidate_min,
        "per_day": config.standard_candidates_per_day,
        "maximum": config.standard_candidate_max,
        "effective_limit": min(
            config.standard_candidate_max,
            max(
                config.standard_candidate_min,
                days * config.standard_candidates_per_day,
            ),
        ),
    }


def review_sample_budget(
    spec: TopicSpec, config: TopicMiningConfig,
) -> dict[str, int]:
    """Return the QA sampling allowance; mandatory review is separate."""
    days = effective_days(spec)
    return {
        "effective_days": days,
        "per_day": config.review_samples_per_day,
        "maximum": config.review_sample_max,
        "sample_limit": min(
            config.review_sample_max,
            days * config.review_samples_per_day,
        ),
    }
