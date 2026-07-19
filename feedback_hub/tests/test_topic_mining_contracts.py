from datetime import datetime

import pytest

from feedback_hub.topic_mining.contracts import (
    topic_spec_hash,
    topic_spec_json_schema,
    validate_topic_spec,
)


def valid_spec():
    return {
        "schema_version": 1,
        "topic_name": "全屏时工具栏不隐藏",
        "objective": "找出视频或游戏全屏时输入法工具栏仍显示的反馈",
        "scope": {
            "start_time": "2026-01-16T00:00:00+08:00",
            "end_time": "2026-07-16T14:00:00+08:00",
            "platforms": ["Win"],
            "products": ["微信输入法"],
        },
        "unit": "feedback",
        "inclusion_criteria": ["视频或游戏全屏时工具栏仍显示"],
        "exclusion_criteria": ["Windows 系统任务栏不隐藏"],
        "positive_examples": ["全屏游戏时输入法工具条一直挡着"],
        "negative_examples": ["进入游戏后黑屏"],
        "lexical_hints": {"objects": ["工具栏"], "contexts": ["全屏", "游戏"]},
        "classification_labels": [
            {"id": "matched", "meaning": "明确符合专题定义"},
            {"id": "not_matched", "meaning": "不符合或证据不足"},
        ],
        "output": {
            "preferred_format": "xlsx",
            "required_fields": ["feedback_text", "feedback_time", "source_url"],
        },
    }


def test_validate_topic_spec_normalizes_timezone_and_deduplicates_terms():
    raw = valid_spec()
    raw["lexical_hints"]["contexts"].append("全屏")
    spec = validate_topic_spec(raw)
    assert spec.scope.start_time == datetime.fromisoformat("2026-01-16T00:00:00+08:00")
    assert spec.lexical_hints["contexts"] == ("全屏", "游戏")


@pytest.mark.parametrize("field", ["topic_name", "objective", "inclusion_criteria", "exclusion_criteria"])
def test_validate_topic_spec_rejects_missing_semantic_boundary(field):
    raw = valid_spec()
    raw.pop(field)
    with pytest.raises(ValueError, match=field):
        validate_topic_spec(raw)


def test_validate_topic_spec_rejects_naive_or_reversed_time():
    raw = valid_spec()
    raw["scope"]["start_time"] = "2026-01-16T00:00:00"
    with pytest.raises(ValueError, match="timezone"):
        validate_topic_spec(raw)
    raw = valid_spec()
    raw["scope"]["end_time"] = "2026-01-15T00:00:00+08:00"
    with pytest.raises(ValueError, match="end_time"):
        validate_topic_spec(raw)


def test_topic_spec_hash_is_stable_under_mapping_order():
    left = valid_spec()
    right = dict(reversed(list(left.items())))
    assert topic_spec_hash(validate_topic_spec(left)) == topic_spec_hash(validate_topic_spec(right))


def test_schema_forbids_bottom_layer_retrieval_parameters():
    schema = topic_spec_json_schema()
    encoded = str(schema)
    assert "top_k" not in encoded
    assert "similarity_threshold" not in encoded
    assert schema["additionalProperties"] is False
