import hashlib

import pytest

from feedback_hub.topic_mining.contracts import validate_topic_spec
from feedback_hub.topic_mining.run_store import TopicRunStore


@pytest.fixture
def valid_topic_spec():
    return validate_topic_spec(
        {
            "schema_version": 1,
            "topic_name": "工具栏遮挡",
            "objective": "找出全屏时工具栏遮挡的反馈",
            "scope": {
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-02T00:00:00+00:00",
                "platforms": ["Win"],
            },
            "unit": "feedback",
            "inclusion_criteria": ["工具栏遮挡"],
            "exclusion_criteria": ["无关"],
            "positive_examples": [],
            "negative_examples": [],
            "lexical_hints": {"objects": ["工具栏"], "contexts": ["全屏"]},
            "classification_labels": [
                {"id": "matched", "meaning": "符合"},
                {"id": "not_matched", "meaning": "不符合"},
            ],
            "output": {"preferred_format": "jsonl", "required_fields": ["feedback_text"]},
        }
    )


def test_create_run_is_idempotent_for_spec_and_source_watermark(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")

    first = store.create_or_get(valid_topic_spec, source_watermark_ms=1234)
    second = store.create_or_get(valid_topic_spec, source_watermark_ms=1234)

    assert first["run_id"] == second["run_id"]
    assert first["status"] == "pending"
    assert first["created"] is True
    assert second["created"] is False
    assert first["run_id"] == hashlib.sha256(
        f"{first['spec_hash']}:1234".encode()
    ).hexdigest()[:16]


def test_same_spec_with_new_source_watermark_creates_new_run(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")

    assert store.create_or_get(valid_topic_spec, 1234)["run_id"] != store.create_or_get(
        valid_topic_spec, 5678
    )["run_id"]


def test_create_run_uses_independent_database_and_artifact_dir(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")

    run = store.create_or_get(valid_topic_spec, 1234)

    assert (tmp_path / "runs.db").is_file()
    assert run["artifact_dir"] == str(tmp_path / "runs" / run["run_id"])
    assert (tmp_path / "runs" / run["run_id"]).is_dir()
