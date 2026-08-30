from datetime import datetime

import pytest

from feedback_hub.topic_mining.contracts import (
    topic_spec_hash,
    topic_spec_json_schema,
    validate_topic_spec,
)
import feedback_hub.topic_mining.contracts as contracts


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


def test_topic_spec_defaults_mode_to_standard():
    raw = valid_spec()
    raw.pop("mode", None)

    assert validate_topic_spec(raw).mode == "standard"


def test_new_topic_specs_reject_conversation_but_persisted_loader_keeps_history_readable():
    raw = valid_spec()
    raw["unit"] = "conversation"

    with pytest.raises(ValueError, match="unit must be feedback"):
        validate_topic_spec(raw)

    persisted = contracts.load_persisted_topic_spec(raw)
    assert persisted.unit == "conversation"
    assert topic_spec_json_schema()["properties"]["unit"] == {"enum": ["feedback"]}


def test_schema_allows_duplicate_list_values_that_normalizer_deduplicates():
    raw = valid_spec()
    raw["scope"]["platforms"].append("Win")
    raw["lexical_hints"]["contexts"].append("全屏")

    spec = validate_topic_spec(raw)

    assert spec.scope.platforms == ("Win",)
    assert spec.lexical_hints["contexts"] == ("全屏", "游戏")
    assert "uniqueItems" not in str(topic_spec_json_schema())


@pytest.mark.parametrize(
    ("raw_values", "expected"),
    [
        (["Win", "Android", "iOS", "Mac"], ("Win", "Android", "iOS", "Mac")),
        ([" windows ", "WIN端"], ("Win",)),
        (["安卓", "A N D R O I D端"], ("Android",)),
        (["ios端"], ("iOS",)),
        (["macOS", "M A C端"], ("Mac",)),
    ],
)
def test_new_topic_spec_canonicalizes_platform_aliases(raw_values, expected):
    raw = valid_spec()
    raw["scope"]["platforms"] = raw_values

    assert validate_topic_spec(raw).scope.platforms == expected


@pytest.mark.parametrize(
    "unsupported", ["Linux", "PC", "电脑", "苹果", "iPhone", "iPad"],
)
def test_new_topic_spec_rejects_unsupported_or_ambiguous_platform(unsupported):
    raw = valid_spec()
    raw["scope"]["platforms"] = [unsupported]

    with pytest.raises(
        ValueError,
        match=rf"unsupported platform: {unsupported}; supported platforms: Win, Android, iOS, Mac",
    ):
        validate_topic_spec(raw)


def test_alias_and_canonical_platform_have_same_new_run_hash():
    alias = valid_spec()
    alias["scope"]["platforms"] = ["Windows"]
    canonical = valid_spec()

    assert topic_spec_hash(validate_topic_spec(alias)) == topic_spec_hash(
        validate_topic_spec(canonical),
    )


def test_persisted_topic_spec_preserves_legacy_noncanonical_platform_identity():
    raw = valid_spec()
    raw["scope"]["platforms"] = ["Windows"]

    persisted = contracts.load_persisted_topic_spec(raw)

    assert persisted.scope.platforms == ("Windows",)
    assert persisted.to_dict()["scope"]["platforms"] == ["Windows"]


def test_validate_topic_spec_rejects_unknown_lexical_hint_keys():
    raw = valid_spec()
    raw["lexical_hints"]["symptoms"] = ["遮挡"]

    with pytest.raises(ValueError, match="lexical_hints"):
        validate_topic_spec(raw)


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


def test_required_output_fields_are_limited_to_the_export_contract():
    raw = valid_spec()
    raw["output"]["required_fields"] = ["feedback_text", "not_exported"]

    with pytest.raises(ValueError, match="unsupported fields: not_exported"):
        validate_topic_spec(raw)

    schema = topic_spec_json_schema()
    field_schema = schema["properties"]["output"]["properties"][
        "required_fields"
    ]["items"]
    assert set(field_schema["enum"]) == {
        "feedback_text", "feedback_time", "source_url",
    }
