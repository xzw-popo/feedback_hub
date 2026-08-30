"""Schema constants and helpers for feedback label v2."""
from __future__ import annotations

SCHEMA_VERSION = "feedback_label_v2"

FEEDBACK_TYPES = {
    "bug_problem",
    "feature_request",
    "improvement_request",
    "question_help",
    "sentiment_only",
    "irrelevant_invalid",
    "mixed",
}

DETAIL_REQUIRED_TYPES = {
    "bug_problem",
    "feature_request",
    "improvement_request",
}

PRODUCT_AREAS = {
    "input_core",
    "voice_input",
    "emoji_expression",
    "ai_generation",
    "clipboard_sync",
    "keyboard_ui",
    "dictionary_phrases",
    "permissions_privacy",
    "performance_stability",
    "install_update",
    "cross_app_compatibility",
    "account_sync",
    "other_low_priority",
    "other_unknown",
}

ISSUE_PATTERNS = {
    "unavailable_or_broken",
    "incorrect_or_poor_result",
    "missing_or_unsupported",
    "hard_to_use_or_trigger",
    "performance_problem",
    "layout_or_display_problem",
    "compatibility_problem",
    "data_or_sync_problem",
    "other_unknown",
}

EVIDENCE_SIGNALS = {
    "has_actual_behavior",
    "has_expected_behavior",
    "has_context",
    "has_repro_steps",
    "has_scenario",
    "has_comparison",
    "has_workaround",
    "vague",
}

VALUE_SIGNALS = {
    "clear_actionable",
    "strong_pain",
    "workflow_blocker",
    "new_scenario",
    "regression_suspected",
    "competitor_mentioned",
    "retention_risk",
    "positive_signal",
}

OBSERVABLE_IMPACTS = {
    "blocked",
    "degraded",
    "friction",
    "preference",
    "unknown",
}

ACTIONABILITY_VALUES = {
    "actionable",
    "external_constraint",
    "product_policy",
    "insufficient_info",
    "unknown",
}


def should_skip_detail(feedback_type: str) -> bool:
    return feedback_type == "irrelevant_invalid"
