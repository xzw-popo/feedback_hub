from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_topic_boundary_refinement import (
    build_artifact_paths,
    build_refinement_batches,
    build_retry_batches,
    summarize_refinement,
)


def _daily(topic_id: str, title: str, feature: str = "voice_input") -> dict:
    return {
        "daily_topic_id": topic_id,
        "source_date": "2026-07-13",
        "title": title,
        "description": f"{title}的详细描述",
        "conversation_ids": [f"conversation:{topic_id}"],
        "conversation_count": 1,
        "issue_unit_count": 1,
        "feature_candidate_counts": {feature: 1},
        "feedback_type_counts": {"bug_problem": 1},
        "platform_counts": {"Win": 1},
        "evidence_links": ["https://example.test/chat"],
        "members": [{
            "summary": title,
            "evidence_spans": [title],
            "evidence_links": ["https://example.test/chat"],
            "has_media_evidence": False,
        }],
    }


def test_build_refinement_batches_enriches_facts_and_covers_targets_once() -> None:
    current_one = _daily("d1", "Win语音输入不上屏")
    current_two = _daily("d2", "仅说无法使用", "unknown_feature")
    historical = [
        _daily("h1", "Mac语音输入不上屏"),
        _daily("h2", "候选词数量不能调整", "candidate_suggestions"),
    ]
    targeted = [
        {
            "daily_topic_id": "d1",
            "daily_topic": current_one,
            "candidates": [{
                "topic_id": "topic:000001",
                "canonical_title": "旧标题",
                "cosine_similarity": 0.83,
            }],
            "baseline_decision": {"verdict": "same_topic"},
            "selection_reasons": ["low_confidence_same_topic"],
        },
        {
            "daily_topic_id": "d2",
            "daily_topic": current_two,
            "candidates": [],
            "baseline_decision": {"verdict": "new_topic"},
            "selection_reasons": ["low_information_candidate"],
        },
    ]

    batches = build_refinement_batches(targeted, historical, max_batch_size=1)

    assert [batch["batch_id"] for batch in batches] == [
        "refine:0001", "refine:0002"
    ]
    items = [item for batch in batches for item in batch["items"]]
    assert [item["daily_topic_id"] for item in items] == ["d1", "d2"]
    assert all("baseline_decision" not in item for item in items)
    assert items[0]["daily_topic"]["feature_candidate_counts"] == {"voice_input": 1}
    assert items[0]["daily_topic"]["evidence_links"] == ["https://example.test/chat"]
    assert items[0]["candidates"][0]["source_daily_topic_id"] == "h1"
    assert items[0]["candidates"][0]["platform_counts"] == {"Win": 1}
    assert items[0]["candidates"][0]["cosine_similarity"] == 0.83
    assert items[1]["candidates"] == []


def test_build_refinement_batches_rejects_duplicate_targets() -> None:
    row = {
        "daily_topic_id": "d1",
        "daily_topic": _daily("d1", "问题"),
        "candidates": [],
        "selection_reasons": [],
    }
    with pytest.raises(ValueError, match="duplicate"):
        build_refinement_batches([row, row], [], max_batch_size=10)


def test_build_artifact_paths_uses_isolated_contract_names(tmp_path: Path) -> None:
    paths = build_artifact_paths(tmp_path)

    assert paths["targeted"].name == "targeted_topics.jsonl"
    assert paths["model_rows"].name == "revised_lifecycle_model_rows.jsonl"
    assert paths["pool"].name == "low_information_pool.jsonl"
    assert paths["manifest"].name == "manifest.json"
    assert set(path.parent for path in paths.values()) == {tmp_path}


def test_build_retry_batches_splits_only_failed_primary_rows() -> None:
    model_rows = [
        {
            "batch_id": "refine:0001",
            "items": [{"daily_topic_id": f"d{i}"} for i in range(1, 8)],
            "decisions": [],
            "match_error": None,
            "match_parse_error": "invalid JSON",
        },
        {
            "batch_id": "refine:0002",
            "items": [{"daily_topic_id": "d8"}],
            "decisions": [{"daily_topic_id": "d8", "verdict": "new_topic"}],
            "match_error": None,
            "match_parse_error": None,
        },
    ]

    retries = build_retry_batches(model_rows, retry_batch_size=3)

    assert [row["batch_id"] for row in retries] == [
        "retry:refine:0001:0001",
        "retry:refine:0001:0002",
        "retry:refine:0001:0003",
    ]
    assert [
        item["daily_topic_id"] for batch in retries for item in batch["items"]
    ] == [f"d{i}" for i in range(1, 8)]


def test_summarize_refinement_reconciles_changes_and_flags() -> None:
    targeted = [
        {"daily_topic_id": "d1", "selection_reasons": ["baseline_uncertain"]},
        {"daily_topic_id": "d2", "selection_reasons": ["low_information_candidate"]},
    ]
    baseline = [
        {"daily_topic_id": "d1", "verdict": "uncertain", "historical_topic_id": "t1"},
        {"daily_topic_id": "d2", "verdict": "new_topic", "historical_topic_id": None},
    ]
    revised = [
        {"daily_topic_id": "d1", "verdict": "same_topic", "historical_topic_id": "t1"},
        {"daily_topic_id": "d2", "verdict": "low_information", "historical_topic_id": None},
    ]
    full = [{**row, "decision_source": "targeted_refinement"} for row in revised]
    pool = [{"daily_topic_id": "d2", "has_media_evidence": True}]
    audit = [
        {"daily_topic_id": "d1", "audit_flags": ["many_to_one_same_topic"]},
        {"daily_topic_id": "d2", "audit_flags": ["low_information_media"]},
    ]

    summary = summarize_refinement(
        targeted,
        baseline,
        revised,
        full,
        pool,
        audit,
        {"batches": 1, "call_failed": 0, "parse_failed": 0},
    )

    assert summary["targeted_topics"] == 2
    assert summary["changed_decisions"] == 2
    assert summary["old_verdict_counts"] == {"new_topic": 1, "uncertain": 1}
    assert summary["new_verdict_counts"] == {"low_information": 1, "same_topic": 1}
    assert summary["full_overlaid_decisions"] == 2
    assert summary["low_information_pool"] == 1
    assert summary["low_information_media"] == 1
    assert summary["audit_flag_counts"] == {
        "low_information_media": 1,
        "many_to_one_same_topic": 1,
    }
