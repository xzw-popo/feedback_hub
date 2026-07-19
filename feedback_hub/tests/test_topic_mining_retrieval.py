from __future__ import annotations

import json

import pytest

from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.retrieval import (
    BM25Index,
    VectorIndexStaleError,
    build_semantic_queries,
    hybrid_recall,
    tokenize_text,
    verify_vector_watermark,
)
from feedback_hub.topic_mining.vector_client import VectorHit


@pytest.fixture
def valid_topic_spec():
    return validate_topic_spec({
        "schema_version": 1,
        "topic_name": "工具栏遮挡",
        "objective": "找出全屏时工具栏遮挡的反馈",
        "scope": {
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T04:00:00+00:00",
            "platforms": ["Win"],
        },
        "unit": "feedback",
        "inclusion_criteria": ["工具栏遮挡"],
        "exclusion_criteria": ["无关"],
        "positive_examples": ["悬浮栏位置乱跑"],
        "negative_examples": ["进入游戏后黑屏"],
        "lexical_hints": {"objects": ["工具栏", "悬浮栏"], "contexts": ["全屏", "游戏"]},
        "classification_labels": [
            {"id": "matched", "meaning": "符合"},
            {"id": "not_matched", "meaning": "不符合"},
        ],
        "output": {"preferred_format": "jsonl", "required_fields": ["feedback_text"]},
    })


def recall_config():
    return TopicMiningConfig(bm25_top_k=10, vector_top_k=10, candidate_limit=10, rrf_k=60)


def test_tokenization_and_bm25_ranking_are_deterministic():
    assert tokenize_text("Windows 全屏工具栏 UI") == tokenize_text("windows 全屏工具栏 ui")
    index = BM25Index.from_texts(["全屏工具栏遮挡", "全屏工具栏", "黑屏"])

    first = index.search("全屏工具栏遮挡", top_k=2)
    second = index.search("全屏工具栏遮挡", top_k=2)

    assert [hit.index for hit in first] == [0, 1]
    assert first == second


def test_semantic_query_order_is_objective_then_examples(valid_topic_spec):
    assert build_semantic_queries(valid_topic_spec) == [
        {"id": "objective:0", "text": "找出全屏时工具栏遮挡的反馈", "kind": "positive"},
        {"id": "positive:0", "text": "悬浮栏位置乱跑", "kind": "positive"},
        {"id": "negative:0", "text": "进入游戏后黑屏", "kind": "negative"},
    ]


def test_hybrid_recall_unions_channels_but_intersects_hard_scope(valid_topic_spec):
    items = [
        {"item_id": "in-scope", "text": "游戏全屏工具栏挡着"},
        {"item_id": "keyword-only", "text": "悬浮栏位置乱跑"},
    ]
    vector_hits = [
        VectorHit("in-scope", "positive:0", 0.81, 1),
        VectorHit("out-of-scope", "positive:0", 0.99, 2),
    ]
    plan = hybrid_recall(items, valid_topic_spec, vector_hits=vector_hits, config=recall_config())

    assert {row.item_id for row in plan.candidates} == {"in-scope", "keyword-only"}
    assert "out-of-scope" in plan.rejected_out_of_scope_ids


def test_vector_only_candidate_is_not_marked_as_final_match(valid_topic_spec):
    plan = hybrid_recall(
        [{"item_id": "v1", "text": "那个条一直挡着"}],
        valid_topic_spec,
        vector_hits=[VectorHit("v1", "positive:0", 0.82, 1)],
        config=recall_config(),
    )

    assert plan.candidates[0].channels == ("vector",)
    assert not hasattr(plan.candidates[0], "matched")


def test_rrf_ties_break_by_item_id_and_negative_hits_do_not_score(valid_topic_spec):
    plan = hybrid_recall(
        [{"item_id": "z", "text": "无关"}, {"item_id": "a", "text": "无关"}],
        valid_topic_spec,
        vector_hits=[
            VectorHit("z", "positive:0", 0.9, 1),
            VectorHit("a", "positive:0", 0.8, 1),
            VectorHit("a", "negative:0", 0.99, 1),
        ],
        config=recall_config(),
    )

    assert [row.item_id for row in plan.candidates] == ["a", "z"]
    assert plan.candidates[0].negative_query_hits == ("negative:0",)
    assert plan.candidates[0].fused_score == plan.candidates[1].fused_score


def test_configured_candidate_limit_and_auditable_artifacts(valid_topic_spec, tmp_path):
    plan = hybrid_recall(
        [{"item_id": "b", "text": "无关"}, {"item_id": "a", "text": "无关"}],
        valid_topic_spec,
        vector_hits=[VectorHit("b", "positive:0", 0.9, 1), VectorHit("a", "positive:0", 0.8, 2)],
        config=TopicMiningConfig(data_dir=tmp_path, candidate_limit=1),
        artifact_dir=tmp_path / "artifacts",
        source_watermark_ms=100,
        vector_watermark_ms=100,
    )

    assert [row.item_id for row in plan.candidates] == ["b"]
    rows = (tmp_path / "artifacts" / "recall_candidates.jsonl").read_text(encoding="utf-8").splitlines()
    manifest = json.loads((tmp_path / "artifacts" / "recall_manifest.json").read_text(encoding="utf-8"))
    assert json.loads(rows[0])["channel_ranks"]["positive:0"] == 1
    assert manifest["source_watermark_ms"] == 100
    assert manifest["vector_watermark_ms"] == 100
    assert manifest["configuration_version"] == 1


def test_stale_vector_watermark_blocks_hybrid_recall():
    with pytest.raises(VectorIndexStaleError):
        verify_vector_watermark(source_watermark_ms=10_000_000, vector_watermark_ms=1, max_lag_seconds=3600)


def test_missing_vector_watermark_blocks_hybrid_recall():
    with pytest.raises(VectorIndexStaleError):
        verify_vector_watermark(source_watermark_ms=1, vector_watermark_ms=None, max_lag_seconds=3600)
