"""Deterministic diversity selection for recall-only topic candidates."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

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
