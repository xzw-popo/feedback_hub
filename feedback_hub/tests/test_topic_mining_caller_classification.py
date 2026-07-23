import pytest

from feedback_hub.topic_mining.caller_classification import (
    DecisionValidationError,
    validate_decision,
)


def test_valid_caller_decision_preserves_exact_evidence():
    decision = validate_decision(
        {
            "item_id": "a",
            "label": "matched",
            "reason": "  明确命中  ",
            "evidence": ["工具栏还在"],
        },
        {"item_id": "a", "text": "全屏以后工具栏还在"},
        [{"text": "补充上下文"}],
    )

    assert decision.to_dict() == {
        "item_id": "a",
        "label": "matched",
        "reason": "明确命中",
        "evidence": ["工具栏还在"],
    }


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (
            {
                "item_id": "x", "label": "matched", "reason": "明确",
                "evidence": ["原文"],
            },
            "unknown_item",
        ),
        (
            {
                "item_id": "a", "label": "maybe", "reason": "明确",
                "evidence": [],
            },
            "invalid_label",
        ),
        (
            {
                "item_id": "a", "label": "not_matched", "reason": "",
                "evidence": [],
            },
            "reason_required",
        ),
        (
            {
                "item_id": "a", "label": "matched", "reason": "明确",
                "evidence": [],
            },
            "evidence_required",
        ),
        (
            {
                "item_id": "a", "label": "matched", "reason": "明确",
                "evidence": ["概括而非原文"],
            },
            "evidence_not_grounded",
        ),
        (
            {
                "item_id": "a", "label": "not_matched", "reason": "排除",
                "evidence": ["文本"],
            },
            "evidence_not_allowed",
        ),
        (
            {
                "item_id": "a", "label": "not_matched", "reason": "排除",
                "evidence": [], "confidence": 0.9,
            },
            "unsupported_fields",
        ),
    ],
)
def test_invalid_caller_decision_returns_stable_item_code(raw, code):
    with pytest.raises(DecisionValidationError) as error:
        validate_decision(
            raw,
            {"item_id": "a", "text": "全屏以后工具栏还在"},
            [{"text": "补充上下文"}],
        )

    assert error.value.code == code
