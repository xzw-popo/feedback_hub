from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feedback_hub.tests.test_topic_mining_contracts import valid_spec
from feedback_hub.topic_mining.budgets import (
    candidate_budget,
    effective_days,
    review_sample_budget,
)
from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.contracts import validate_topic_spec


def _spec_with_elapsed_ms(elapsed_ms: int, *, mode: str = "standard"):
    raw = valid_spec()
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    raw["scope"]["start_time"] = start.isoformat()
    raw["scope"]["end_time"] = (start + timedelta(milliseconds=elapsed_ms)).isoformat()
    raw["mode"] = mode
    return validate_topic_spec(raw)


@pytest.mark.parametrize(("elapsed_ms", "days", "limit"), [
    (60 * 60 * 1000, 1, 100),
    (24 * 60 * 60 * 1000, 1, 100),
    (24 * 60 * 60 * 1000 + 1, 2, 160),
    (3 * 24 * 60 * 60 * 1000, 3, 240),
    (6 * 24 * 60 * 60 * 1000, 6, 480),
    (7 * 24 * 60 * 60 * 1000, 7, 500),
    (180 * 24 * 60 * 60 * 1000, 180, 500),
])
def test_standard_candidate_budget_scales_by_elapsed_days(elapsed_ms, days, limit):
    budget = candidate_budget(_spec_with_elapsed_ms(elapsed_ms), TopicMiningConfig())

    assert budget == {
        "mode": "standard",
        "effective_days": days,
        "minimum": 100,
        "per_day": 80,
        "maximum": 500,
        "effective_limit": limit,
    }


def test_exhaustive_budget_keeps_5000_safety_limit():
    budget = candidate_budget(
        _spec_with_elapsed_ms(24 * 60 * 60 * 1000, mode="exhaustive"),
        TopicMiningConfig(),
    )

    assert budget == {
        "mode": "exhaustive",
        "effective_days": 1,
        "maximum": 5_000,
        "effective_limit": 5_000,
    }


def test_review_sample_budget_scales_to_80_maximum():
    one_day = _spec_with_elapsed_ms(60 * 60 * 1000)
    seven_days = _spec_with_elapsed_ms(7 * 24 * 60 * 60 * 1000)

    assert effective_days(one_day) == 1
    assert review_sample_budget(one_day, TopicMiningConfig()) == {
        "effective_days": 1, "per_day": 20, "maximum": 80, "sample_limit": 20,
    }
    assert review_sample_budget(seven_days, TopicMiningConfig())["sample_limit"] == 80
