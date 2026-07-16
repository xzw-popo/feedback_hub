"""Relate atomic topics and run strict topic-level daily insight editing."""
from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np


_RELATION_PRIORITY = {
    "same_stable_topic": 0,
    "parent_subtopic": 1,
    "semantic_similarity": 2,
}


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
