# Feedback Label V2 Design

## Goal

Build a new feedback labeling system that supports user-feedback mining reports while keeping LLM labeling difficulty low. The labels should explain what users are talking about, aggregate cleanly for trend analysis, and provide enough evidence to select representative feedback for reports.

## Design Principles

1. Label observable facts, not final conclusions.
2. Use statistics and clustering to produce insights such as "new issue", "rising trend", and "high-value opportunity".
3. Do not force every field onto every feedback item.
4. Keep the first version small enough to evaluate manually.
5. Preserve existing `L1`, `L2`, and `severity` during migration; add v2 labels first, then clean redundant legacy fields after downstream consumers move to v2.

## Labeling Flow

Each feedback item is labeled in two stages.

Stage 1 decides whether the item should continue through detailed labeling:

- `bug_problem`: user reports an issue, abnormal behavior, or broken function.
- `feature_request`: user asks for a new capability or new supported scenario.
- `improvement_request`: user asks to improve an existing capability.
- `question_help`: user asks how to use something.
- `sentiment_only`: user only praises or complains without actionable detail.
- `irrelevant_invalid`: test data, spam, unrelated content, or unusable text.

Stage 2 only runs for feedback types that can support analysis:

- Always run for `bug_problem`, `feature_request`, and `improvement_request`.
- Run conditionally for `question_help` and `sentiment_only` only when the target area is clear.
- Skip for `irrelevant_invalid`.

Skipped detailed fields must be explicit:

```json
{
  "feedback_type": "irrelevant_invalid",
  "product_area": null,
  "issue_pattern": null,
  "skip_reason": "irrelevant_invalid"
}
```

## Core Labels

### `feedback_type`

Single choice. Required for every item.

Allowed values:

- `bug_problem`
- `feature_request`
- `improvement_request`
- `question_help`
- `sentiment_only`
- `irrelevant_invalid`
- `mixed`

`mixed` is allowed when a feedback item clearly contains multiple valid intents, such as a bug report plus an explicit feature request. The output should still include the primary analysis target in `primary_feedback_type`.

### `product_area`

Multi-choice for valid analytical feedback; null for invalid or irrelevant items.

Initial allowed values:

- `input_core`: typing, candidates, pinyin, correction, committing text.
- `voice_input`: voice input, speech recognition, speech-to-text.
- `emoji_expression`: stickers, emoji, kaomoji, expression recommendations.
- `ai_generation`: AI polish, ask-AI, text organization, generated content.
- `clipboard_sync`: clipboard, cross-device paste, sync-related paste flows.
- `keyboard_ui`: keyboard layout, height, toolbar, buttons, interaction surface.
- `dictionary_phrases`: dictionary, hot words, custom phrases, user words.
- `permissions_privacy`: permission prompts, privacy concerns, trust concerns.
- `performance_stability`: lag, freeze, battery drain, heat, crash when area is unclear.
- `install_update`: install, upgrade, version compatibility, package problems.
- `cross_app_compatibility`: problems tied to a specific app or host environment.
- `account_sync`: account only when it affects sync, data recovery, or cross-device flows.
- `other_low_priority`: recognizable but low-priority areas such as skins or pure visual customization.
- `other_unknown`: valid feedback but target area cannot be determined.

Theme and pure account-login feedback are intentionally not first-class focus areas for the current product mining report. They should be routed to `other_low_priority` unless they affect sync, data recovery, permissions, or compatibility.

### `issue_pattern`

Single or multi-choice for `bug_problem`, `feature_request`, and `improvement_request`; null for invalid or unrelated items.

Initial allowed values:

- `unavailable_or_broken`: cannot use, no response, crash, freeze, feature broken.
- `incorrect_or_poor_result`: wrong result, poor recognition, poor recommendation, low generated quality.
- `missing_or_unsupported`: missing capability, unsupported scenario, unsupported platform or app.
- `hard_to_use_or_trigger`: hard to find, path too deep, unclear trigger, unstable trigger, cumbersome operation.
- `performance_problem`: slow, laggy, delayed, battery drain, heat.
- `layout_or_display_problem`: layout error, clipping, overlap, display missing, visual misplacement.
- `compatibility_problem`: specific system, device, version, host app, or environment problem.
- `data_or_sync_problem`: sync failure, data loss, dictionary loss, clipboard sync loss.
- `other_unknown`: valid analytical feedback but the issue pattern cannot be determined.

The experiment must check whether these classes are too broad or still coupled. Privacy, content safety, notification disturbance, and trust concerns remain observation candidates before becoming first-class `issue_pattern` values.

## Report Support Labels

### `evidence_signal`

Multi-choice. Used to choose representative feedback and assess whether an insight is explainable.

Allowed values:

- `has_actual_behavior`: describes what happened.
- `has_expected_behavior`: describes what the user expected.
- `has_context`: includes platform, version, device, app, scenario, or environment.
- `has_repro_steps`: includes reproduction steps.
- `has_scenario`: includes a concrete usage scenario.
- `has_comparison`: compares with competitor, system keyboard, old version, or another platform.
- `has_workaround`: describes a workaround.
- `vague`: too little detail to explain confidently.

This field is useful even when the labeler is unsure about product area, because it helps the report pick examples that users can click into and understand.

## Experimental Labels

These fields are emitted during experiments but are not allowed to drive core report metrics until manual evaluation proves they are stable.

### `value_signal`

Multi-choice. Candidate values:

- `clear_actionable`: maps to a product or engineering action.
- `strong_pain`: user expresses strong pain.
- `workflow_blocker`: blocks an end-to-end workflow.
- `new_scenario`: describes a scenario not already covered by common usage.
- `regression_suspected`: suggests a new version or recent change made behavior worse.
- `competitor_mentioned`: mentions a competitor or alternative.
- `retention_risk`: says they will stop using, uninstall, or switch.
- `positive_signal`: clearly praises a capability or advantage.

### `observable_impact`

Single choice. It describes only the impact visible in the user text, not product priority.

- `blocked`: user says they cannot complete the task.
- `degraded`: task can be completed but quality or efficiency is meaningfully worse.
- `friction`: minor inconvenience or annoyance.
- `preference`: personal preference without clear task impact.
- `unknown`: impact cannot be judged from text.

### `actionability`

Single choice. It describes whether the issue appears actionable from the feedback text.

- `actionable`: text points to a likely product or engineering action.
- `external_constraint`: text indicates OS, third-party app, policy, or environment limitations.
- `product_policy`: likely caused by an intentional product decision.
- `insufficient_info`: not enough information to act.
- `unknown`: cannot determine.

The first experiment should measure whether `observable_impact` and `actionability` are reliable enough. If they are noisy, they remain report-side derived signals instead of stable database labels.

## Output Shape

The LLM should output JSON only:

```json
{
  "schema_version": "feedback_label_v2",
  "feedback_type": "bug_problem",
  "primary_feedback_type": "bug_problem",
  "product_area": ["voice_input"],
  "issue_pattern": ["incorrect_or_poor_result"],
  "evidence_signal": ["has_actual_behavior", "has_context"],
  "value_signal": ["clear_actionable"],
  "observable_impact": "degraded",
  "actionability": "unknown",
  "evidence_span": "语音识别经常把字识别错",
  "reason": "反馈语音识别结果不准确",
  "confidence": 0.82
}
```

For skipped items:

```json
{
  "schema_version": "feedback_label_v2",
  "feedback_type": "irrelevant_invalid",
  "primary_feedback_type": "irrelevant_invalid",
  "product_area": null,
  "issue_pattern": null,
  "evidence_signal": ["vague"],
  "value_signal": [],
  "observable_impact": "unknown",
  "actionability": "insufficient_info",
  "skip_reason": "irrelevant_invalid",
  "evidence_span": "测试",
  "reason": "测试文本，无有效反馈",
  "confidence": 0.96
}
```

## Experiment Plan

The first implementation slice is an offline experiment, not a production backfill.

1. Add versioned taxonomy and prompt assets under `feedback_hub/tagger/v2/`.
2. Add a parser and validator that rejects invalid enum values and malformed JSON.
3. Add deterministic keyword helpers only for low-risk hints such as invalid text and obvious platform or area keywords.
4. Add a small CLI that labels sampled feedback to JSONL without writing to the database.
5. Evaluate on manually reviewed samples before any schema migration or full backfill.

Experiment metrics:

- JSON parse success rate.
- Invalid enum rate.
- Skip rate by `feedback_type`.
- Distribution of `product_area` and `issue_pattern`.
- Manual agreement on `feedback_type`, `product_area`, and `issue_pattern`.
- Usefulness of `evidence_signal` for selecting report examples.
- Noise rate of `observable_impact` and `actionability`.

## Database Migration Strategy

V2 labels should first land in a separate structure, not overwrite legacy fields.

Preferred storage:

- A new `feedback_label_v2` table keyed by feedback id and label version.
- Store stable columns for common filters: `feedback_type`, `primary_feedback_type`, `confidence`, `created_at`.
- Store the complete JSON result for multi-value fields and experimental fields.

Legacy cleanup happens after report, search, and dashboard consumers stop depending on `message_label.L1`, `message_label.L2`, and `message_label.severity`.

Cleanup sequence:

1. Add v2 labels and keep legacy labels.
2. Switch experiment reports to v2.
3. Switch patrol report aggregations to v2.
4. Add a compatibility view or mapping if old dashboards still need L1/L2/severity.
5. Remove redundant legacy fields only after confirming no active consumer uses them.

## Non-Goals

- Do not label "new issue" at item level.
- Do not label "rising trend" at item level.
- Do not infer root cause from user text.
- Do not assign owner or team without a maintained owner map.
- Do not use experimental fields as hard report filters until evaluation supports them.

