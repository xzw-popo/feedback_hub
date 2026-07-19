from __future__ import annotations

import pytest

from feedback_hub.topic_mining.classifier import ClassificationResult
from feedback_hub.topic_mining.retrieval import RecallHit
from feedback_hub.topic_mining.review import (
    apply_review_overrides,
    build_review_queue,
    persist_review_artifacts,
)


def matched(item_id: str, confidence: float, **changes) -> ClassificationResult:
    values = {"item_id": item_id, "label": "matched", "confidence": confidence, "evidence": ("工具栏",), "reason": "符合", "needs_review": False}
    values.update(changes)
    return ClassificationResult(**values)


def rejected(item_id: str, confidence: float) -> ClassificationResult:
    return ClassificationResult(item_id, "not_matched", confidence, (), "不符合", False)


def recall(*, channels=("bm25",), negative_query_hits=()) -> RecallHit:
    return RecallHit("unused", {"item_id": "unused", "text": "text", "source_url": "https://example.test/feedback/unused"}, channels, 0.0, 1, {}, {}, (), negative_query_hits)


def test_review_queue_includes_vector_only_match_and_negative_conflict():
    queue = build_review_queue(
        run_id="topic_1",
        classifications=[matched("a", 0.91), matched("b", 0.92)],
        recall_by_id={"a": recall(channels=("vector",)), "b": recall(negative_query_hits=("negative:0",))},
        per_label_sample=0,
    )
    assert {row["item_id"] for row in queue} == {"a", "b"}
    assert queue[0]["review_reasons"] == ["vector_only_match"]
    assert queue[1]["review_reasons"] == ["negative_query_conflict"]
    assert queue[0]["source_item"]["text"] == "text"
    assert queue[0]["source_url"] == "https://example.test/feedback/unused"


def test_review_queue_includes_all_mandatory_reasons_and_stable_samples():
    values = [
        matched("low", 0.74),
        matched("requested", 0.9, needs_review=True),
        rejected("reject", 0.9),
        rejected("other", 0.4),
    ]
    first = build_review_queue("run", values, {row.item_id: recall() for row in values}, per_label_sample=1)
    second = build_review_queue("run", values, {row.item_id: recall() for row in values}, per_label_sample=1)
    by_id = {row["item_id"]: row for row in first}
    assert "low_confidence_match" in by_id["low"]["review_reasons"]
    assert "classifier_requested_review" in by_id["requested"]["review_reasons"]
    assert "high_confidence_reject_sample" in by_id["reject"]["review_reasons"]
    assert any("deterministic_label_sample" in row["review_reasons"] for row in first)
    assert first == second


def test_overrides_are_separate_and_cannot_change_source_text():
    merged = apply_review_overrides(
        [matched("a", 0.6)],
        [{"item_id": "a", "label": "not_matched", "reason": "系统任务栏", "reviewer": "skill-ai"}],
    )
    assert merged[0].label == "not_matched"
    assert merged[0].source == "review_override"
    assert merged[0].evidence == ("工具栏",)


def test_persisted_queue_and_overrides_use_separate_auditable_paths(tmp_path):
    queue = build_review_queue("run", [matched("a", 0.5)], {"a": recall()}, per_label_sample=0)
    overrides = [{"item_id": "a", "label": "not_matched", "reason": "审核排除", "reviewer": "skill-ai"}]
    persist_review_artifacts(tmp_path, queue, overrides)
    assert (tmp_path / "review_queue.jsonl").read_text(encoding="utf-8").strip().startswith('{"classification_source"')
    assert (tmp_path / "review_overrides.jsonl").read_text(encoding="utf-8").strip().endswith('"reviewer": "skill-ai"}')


@pytest.mark.parametrize("overrides, message", [
    ([{"item_id": "missing", "label": "matched", "reason": "x", "reviewer": "a"}], "unknown"),
    ([{"item_id": "a", "label": "bad", "reason": "x", "reviewer": "a"}], "label"),
    ([{"item_id": "a", "label": "matched", "reason": "", "reviewer": "a"}], "reason"),
    ([{"item_id": "a", "label": "matched", "reason": "x", "reviewer": ""}], "reviewer"),
    ([{"item_id": "a", "label": "matched", "reason": "x", "reviewer": "a"}, {"item_id": "a", "label": "matched", "reason": "y", "reviewer": "b"}], "duplicate"),
])
def test_overrides_validate_identity_and_decision(overrides, message):
    with pytest.raises(ValueError, match=message):
        apply_review_overrides([matched("a", 0.9)], overrides)


def test_review_queue_rejects_duplicate_classification_ids():
    with pytest.raises(ValueError, match="duplicate"):
        build_review_queue("run", [matched("a", 0.9), matched("a", 0.8)], {"a": recall()})
