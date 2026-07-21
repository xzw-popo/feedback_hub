"""Deterministic diversity selection for recall-only topic candidates."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from .retrieval import RecallHit


_WEEK_MS = 7 * 24 * 60 * 60 * 1000


def candidate_stratum(hit: RecallHit) -> tuple[int, str, str]:
    """Return a stable time, channel, and semantic-query candidate bucket."""
    week_bucket = int(hit.item["ts_ms"]) // _WEEK_MS
    channel = str(hit.item.get("channel") or "")
    semantic_query = next(
        (query_id for query_id in hit.query_ids if query_id != "bm25"),
        "bm25",
    )
    return week_bucket, channel, semantic_query


def select_diverse_candidates(
    candidates: Sequence[RecallHit], *, limit: int,
) -> Sequence[RecallHit]:
    """Round-robin the best-ranked candidate from each deterministic stratum."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    strata: dict[tuple[int, str, str], list[RecallHit]] = defaultdict(list)
    for candidate in candidates:
        strata[candidate_stratum(candidate)].append(candidate)
    ordered_strata = sorted(
        (
            (stratum, sorted(rows, key=lambda hit: (hit.fused_rank, hit.item_id)))
            for stratum, rows in strata.items()
        ),
        key=lambda entry: (
            entry[1][0].fused_rank,
            entry[1][0].item_id,
            entry[0],
        ),
    )
    selected: list[RecallHit] = []
    round_index = 0
    while len(selected) < limit:
        selected_this_round = False
        for _stratum, rows in ordered_strata:
            if round_index >= len(rows):
                continue
            selected.append(rows[round_index])
            selected_this_round = True
            if len(selected) == limit:
                break
        if not selected_this_round:
            break
        round_index += 1
    return tuple(selected)


def select_representative_results(
    rows: Sequence[Mapping[str, Any]],
    recall_by_id: Mapping[str, RecallHit],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Round-robin reviewed matches through the established recall strata.

    A verified historical run may predate persisted recall artifacts.  Those
    rows retain a stable fallback ``bm25`` stratum so they remain exportable;
    contemporary runs always use their authenticated recall metadata.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    strata: dict[tuple[int, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        item_id = str(row.get("item_id") or "")
        recall = recall_by_id.get(item_id)
        if recall is not None:
            stratum = candidate_stratum(recall)
        else:
            item = row.get("source_item")
            if not isinstance(item, Mapping):
                raise ValueError("result is missing source_item")
            stratum = (
                int(item.get("ts_ms", 0)) // _WEEK_MS,
                str(item.get("channel") or ""),
                "bm25",
            )
        strata[stratum].append(row)

    def rank(row: Mapping[str, Any]) -> tuple[int, str]:
        item_id = str(row.get("item_id") or "")
        recall = recall_by_id.get(item_id)
        return (
            recall.fused_rank if recall is not None else 2**63 - 1,
            item_id,
        )

    ordered_strata = sorted(
        ((stratum, sorted(bucket, key=rank)) for stratum, bucket in strata.items()),
        key=lambda entry: (*rank(entry[1][0]), entry[0]),
    )
    selected: list[dict[str, Any]] = []
    round_index = 0
    while len(selected) < limit:
        selected_this_round = False
        for _stratum, bucket in ordered_strata:
            if round_index >= len(bucket):
                continue
            selected.append(dict(bucket[round_index]))
            selected_this_round = True
            if len(selected) == limit:
                break
        if not selected_this_round:
            break
        round_index += 1
    return selected
