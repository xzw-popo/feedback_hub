# Feedback Label V2 Prompt

You are labeling user feedback for a feedback mining report.

Return JSON only. Do not explain outside JSON.

Schema version: feedback_label_v2

Core rules:

- First decide `feedback_type`.
- Only label `product_area` and `issue_pattern` when the feedback is analytically useful.
- If `feedback_type` is `irrelevant_invalid`, set `product_area` and `issue_pattern` to null.
- If `feedback_type` is `question_help`, leave `issue_pattern` empty unless the user explicitly reports failure, missing support, incompatibility, or inability to complete a task.
- If `feedback_type` is `sentiment_only`, leave `issue_pattern` empty.
- If `feedback_type` is `feature_request`, prefer `issue_pattern=["missing_or_unsupported"]` for new capability or new scenario requests.
- Route pure skin/theme or pure account-login feedback to `other_low_priority` unless it affects sync, data recovery, permissions, or compatibility.
- Do not infer root cause, owner, new issue, or rising trend.
- `observable_impact` and `actionability` describe only what is visible in the feedback text.
- Do not add `vague` when the feedback clearly states actual behavior, expected behavior, context, scenario, comparison, repro steps, or workaround.
- `actionability="actionable"` requires a concrete product, support, or engineering action. Simple questions, vague preferences, and pure sentiment should usually be `insufficient_info` or `unknown`.
- Set `needs_review=true` when confidence is 0.5 or lower, or when product/issue is `other_unknown`.

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
  "confidence": 0.0,
  "needs_review": false,
  "review_reasons": ["low_confidence | unknown_product_area | unknown_issue_pattern"]
}
```
