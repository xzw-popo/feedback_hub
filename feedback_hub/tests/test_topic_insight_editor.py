from __future__ import annotations

import numpy as np

from feedback_hub.topic_discovery.insight_editor import build_candidate_relation_plan


def _candidate(
    candidate_id: str,
    daily_topic_id: str,
    *,
    stable: str | None = None,
    parent_ids: list[str] | None = None,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "daily_topic_id": daily_topic_id,
        "stable_topic_id": stable,
        "parent_topic_ids": parent_ids or [],
        "title": candidate_id,
        "description": candidate_id,
    }


def test_relation_plan_prioritizes_same_stable_topic_and_parent_links() -> None:
    candidates = [
        _candidate("c1", "d1", stable="t1"),
        _candidate("c2", "d2", stable="t1"),
        _candidate("c3", "d3", stable="t2", parent_ids=["t1"]),
    ]
    vectors = np.asarray([[1.0, 0.0], [0.99, 0.01], [0.8, 0.6]], dtype="float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    plan = build_candidate_relation_plan(candidates, ["d1", "d2", "d3"], vectors)

    relation_types = {row["relation_type"] for row in plan["relations"]}
    assert "same_stable_topic" in relation_types
    assert "parent_subtopic" in relation_types
    assert {item["candidate_id"] for item in plan["buckets"][0]["items"]} == {"c1", "c2", "c3"}


def test_relation_plan_never_drops_or_duplicates_candidates() -> None:
    plan = build_candidate_relation_plan(
        [_candidate("c1", "d1"), _candidate("c2", "d2")],
        ["d1", "d2"],
        np.eye(2, dtype="float32"),
    )

    ids = [item["candidate_id"] for bucket in plan["buckets"] for item in bucket["items"]]
    ids += plan["singleton_candidate_ids"]
    assert sorted(ids) == ["c1", "c2"]


def test_relation_plan_bounds_components_deterministically() -> None:
    candidates = [_candidate(f"c{index}", f"d{index}", stable="t1") for index in range(1, 6)]
    vectors = np.asarray([[1.0, index / 100] for index in range(1, 6)], dtype="float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    plan = build_candidate_relation_plan(
        candidates,
        [f"d{index}" for index in range(1, 6)],
        vectors,
        max_bucket_size=3,
    )

    assert max(len(bucket["items"]) for bucket in plan["buckets"]) <= 3
    covered = [item["candidate_id"] for bucket in plan["buckets"] for item in bucket["items"]]
    covered += plan["singleton_candidate_ids"]
    assert sorted(covered) == ["c1", "c2", "c3", "c4", "c5"]
