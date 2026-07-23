"""Strict, item-local validation for caller-AI topic decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_FIELDS = frozenset({"item_id", "label", "reason", "evidence"})


class DecisionValidationError(ValueError):
    """A stable per-item validation failure that does not fail the whole Run."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CallerDecision:
    item_id: str
    label: str
    reason: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "label": self.label,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


def validate_decision(
    raw: Mapping[str, Any],
    candidate: Mapping[str, Any],
    context_items: Sequence[Mapping[str, Any]],
) -> CallerDecision:
    if not isinstance(raw, Mapping):
        raise DecisionValidationError("unsupported_fields")
    if set(raw) != _FIELDS:
        raise DecisionValidationError("unsupported_fields")
    item_id = raw.get("item_id")
    if (
        not isinstance(item_id, str)
        or not item_id
        or item_id != candidate.get("item_id")
    ):
        raise DecisionValidationError("unknown_item")
    label = raw.get("label")
    if label not in {"matched", "not_matched"}:
        raise DecisionValidationError("invalid_label")
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise DecisionValidationError("reason_required")
    evidence = raw.get("evidence")
    if (
        not isinstance(evidence, list)
        or any(not isinstance(value, str) or not value for value in evidence)
    ):
        raise DecisionValidationError(
            "evidence_required" if label == "matched" else "evidence_not_allowed"
        )
    if label == "matched" and not evidence:
        raise DecisionValidationError("evidence_required")
    if label == "not_matched" and evidence:
        raise DecisionValidationError("evidence_not_allowed")
    source_texts = []
    text = candidate.get("text")
    if isinstance(text, str):
        source_texts.append(text)
    for item in context_items:
        if not isinstance(item, Mapping):
            continue
        context_text = item.get("text") or item.get("feedback_text")
        if isinstance(context_text, str):
            source_texts.append(context_text)
    if label == "matched" and any(
        not any(fragment in source for source in source_texts)
        for fragment in evidence
    ):
        raise DecisionValidationError("evidence_not_grounded")
    return CallerDecision(
        item_id=item_id,
        label=label,
        reason=reason.strip(),
        evidence=tuple(evidence),
    )
