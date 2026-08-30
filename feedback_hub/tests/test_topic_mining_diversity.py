from __future__ import annotations

from feedback_hub.topic_mining.diversity import select_diverse_candidates
from feedback_hub.topic_mining.retrieval import RecallHit


_WEEK_MS = 7 * 24 * 60 * 60 * 1000


def recall(item_id: str, *, ts: int, channel: str, query_ids: tuple[str, ...], rank: int) -> RecallHit:
    return RecallHit(
        item_id=item_id,
        item={"item_id": item_id, "text": item_id, "ts_ms": ts, "channel": channel},
        channels=("vector",),
        fused_score=1.0 / rank,
        fused_rank=rank,
        channel_ranks={"vector": rank},
        raw_scores={"vector": 1.0 / rank},
        query_ids=query_ids,
        negative_query_hits=(),
    )


def test_selection_spreads_across_time_channel_and_semantic_query():
    rows = [
        recall("recent-wetype-a", ts=3 * _WEEK_MS, channel="wetype", query_ids=("positive:0",), rank=1),
        recall("recent-wetype-b", ts=3 * _WEEK_MS, channel="wetype", query_ids=("positive:0",), rank=2),
        recall("old-other", ts=1 * _WEEK_MS, channel="other", query_ids=("objective:0",), rank=20),
    ]

    selected = select_diverse_candidates(rows, limit=2)

    assert {row.item_id for row in selected} == {"recent-wetype-a", "old-other"}


def test_selection_is_deterministic_and_uses_bm25_as_fallback_semantic_bucket():
    rows = [
        recall("z", ts=_WEEK_MS, channel="pc", query_ids=("bm25",), rank=1),
        recall("a", ts=_WEEK_MS, channel="pc", query_ids=("bm25",), rank=1),
        recall("other", ts=_WEEK_MS, channel="pc", query_ids=("objective:0",), rank=2),
    ]

    assert [row.item_id for row in select_diverse_candidates(rows, limit=3)] == ["a", "other", "z"]
