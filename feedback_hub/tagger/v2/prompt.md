# Feedback Label V2 Prompt

You are labeling user feedback for a feedback mining report.

Return JSON only. Do not explain outside JSON.

Schema version: feedback_label_v2

Core rules:

- First decide `feedback_type`.
- Only label `product_area` and `issue_pattern` when the feedback is analytically useful.
- If `feedback_type` is `irrelevant_invalid`, set `product_area` and `issue_pattern` to null.
- Route pure skin/theme or pure account-login feedback to `other_low_priority` unless it affects sync, data recovery, permissions, or compatibility.
- Do not infer root cause, owner, new issue, or rising trend.
- `observable_impact` and `actionability` describe only what is visible in the feedback text.

Feedback text:

```text
{{TEXT}}
```

Metadata:

```json
{{METADATA}}
```

Rule hints:

```json
{{RULE_HINTS}}
```

Hints are not final labels. Use them only as conservative clues. If the original
feedback conflicts with the hints, trust the original feedback.

Return this JSON shape:

```json
{
  "schema_version": "feedback_label_v2",
  "feedback_type": "bug_problem | feature_request | improvement_request | question_help | sentiment_only | irrelevant_invalid | mixed",
  "primary_feedback_type": "bug_problem | feature_request | improvement_request | question_help | sentiment_only | irrelevant_invalid",
  "product_area": ["input_core | voice_input | emoji_expression | ai_generation | clipboard_sync | keyboard_ui | dictionary_phrases | permissions_privacy | performance_stability | install_update | cross_app_compatibility | account_sync | other_low_priority | other_unknown"] | null,
  "issue_pattern": ["unavailable_or_broken | incorrect_or_poor_result | missing_or_unsupported | hard_to_use_or_trigger | performance_problem | layout_or_display_problem | compatibility_problem | data_or_sync_problem | other_unknown"] | null,
  "evidence_signal": ["has_actual_behavior | has_expected_behavior | has_context | has_repro_steps | has_scenario | has_comparison | has_workaround | vague"],
  "value_signal": ["clear_actionable | strong_pain | workflow_blocker | new_scenario | regression_suspected | competitor_mentioned | retention_risk | positive_signal"],
  "observable_impact": "blocked | degraded | friction | preference | unknown",
  "actionability": "actionable | external_constraint | product_policy | insufficient_info | unknown",
  "evidence_span": "short original text span",
  "reason": "short reason",
  "confidence": 0.0
}
```
