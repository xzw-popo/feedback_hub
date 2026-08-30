# Topic Memory Eligibility and Boundary Audit Design

**Date:** 2026-07-15

**Status:** Approved for implementation planning

## 1. Purpose

Improve the dynamic topic lifecycle prototype using the findings from the
2026-07-12 to 2026-07-13 cross-day run.

The current system can discover new topics and match recurring topics, but the
review exposed three systematic risks:

1. low-information daily topics can become permanent topic memory;
2. generic shared operations such as sorting or resizing can create false
   same-topic or parent-child relationships across different product objects;
3. platform boundaries are applied inconsistently.

This iteration adds an explicit memory-eligibility decision, tightens lifecycle
boundaries, and audits conflicts after matching. It does not build the final
daily report or run a seven-day history backfill.

## 2. Design Principles

- A daily topic is an auditable evidence unit; it may remain narrower than the
  report-level stable topic.
- Historical topics are mutable memory, not a fixed taxonomy.
- Similarity only recalls candidates. It never determines a lifecycle verdict.
- Insufficient evidence must not create a stable `topic_id`.
- Every daily topic remains accounted for even when it is held outside stable
  memory.
- Raw feedback links and media metadata remain available in every output path.

## 3. Alternatives Considered

### 3.1 Prompt-only tightening

Add boundary rules to the existing lifecycle prompt and rerun it. This is
cheap, but it does not prevent low-information topics from being persisted and
does not expose post-match conflicts.

### 3.2 Layered eligibility, matching, and audit

Add a memory-eligibility verdict, retain the existing lifecycle matcher for
eligible topics, and run deterministic post-match audits. This is the selected
approach because each decision remains independently inspectable and testable.

### 3.3 Global multi-day topic graph

Recluster all historical and current topics together. This may be useful later,
but it is more expensive and makes identity changes harder to explain.

## 4. Processing Flow

```text
daily_topic
  -> memory eligibility
       -> low_information
            -> low_information_pool
            -> media appendix when media evidence exists
       -> eligible
            -> historical candidate recall
            -> lifecycle boundary decision
                 -> same_topic
                 -> new_topic
                 -> possible_subtopic
                 -> uncertain
            -> post-match audit
            -> stable topic event or manual review
```

The first experiment operates on a targeted review set from the completed
413-topic run. It does not rerun conversation extraction or daily topic
discovery.

## 5. Memory Eligibility

### 5.1 New verdict

Add `low_information` as a lifecycle-stage verdict. It means the supplied text
does not identify enough of the feedback object, requested behavior, symptom,
trigger, or expected outcome to form a reusable topic boundary.

Examples include:

- "电脑版无法使用" without a symptom or scenario;
- "某设置自动变回" without naming the setting;
- a short phrase whose intent could be a question, request, or problem;
- an AI or media reference whose visible text does not identify the issue.

`low_information` is not the same as irrelevant. The feedback remains retained
and searchable.

### 5.2 Persistence behavior

A `low_information` decision:

- emits a `low_information_held` event;
- does not allocate a stable `topic_id`;
- writes the daily topic to `low_information_pool.jsonl`;
- preserves all issue units, conversation IDs, evidence links, and reasons;
- sets `has_media_evidence` when any member contains media evidence;
- makes media-bearing entries eligible for the existing media appendix;
- can be upgraded in a later run if new evidence supplies a usable boundary.

### 5.3 Decision source

The experiment uses LLM semantic judgment for memory eligibility because the
failure mode is not reliably detectable with keywords alone. Deterministic
rules may only mark high-precision review candidates; they must not declare a
topic eligible merely because it is long.

Eligibility and lifecycle matching are returned in one model decision to avoid
an additional full model pass. If the verdict is `low_information`, no
historical topic may be selected.

## 6. Lifecycle Boundary Rules

### 6.1 Shared object requirement

`same_topic` and `possible_subtopic` require the current and historical topics
to concern the same product object or capability.

Shared generic operations are insufficient by themselves. The following words
must not create a relationship across different objects:

- sorting;
- size or font adjustment;
- enable/disable or switch;
- entry point or shortcut;
- visibility;
- synchronization;
- customization.

For example, sorting a feature list and sorting saved phrases are separate
topics.

### 6.2 Same-topic rule

Use `same_topic` only when both topics can plausibly be handled by one shared
investigation, fix, or product decision. A narrower trigger may remain the same
topic when it does not require a separate action.

### 6.3 Possible-subtopic rule

Use `possible_subtopic` only when:

1. both topics concern the same object or capability;
2. the historical topic is genuinely broader;
3. the current topic describes an independently actionable symptom, scenario,
   platform variant, or product option beneath that broader topic.

A shared broad domain or operation is not sufficient.

### 6.4 Platform policy

- Bugs and abnormal behavior are platform- or mechanism-specific by default.
  They may be merged only when evidence supports one shared investigation or
  fix.
- Feature requests and improvements may merge across platforms when they map to
  one product decision. Platform remains an attribute for reporting.
- Missing platform information increases uncertainty; it must not be guessed.

## 7. Post-Match Audit

The audit does not rewrite model decisions. It emits review flags and preserves
the original result.

### 7.1 Audit flags

- `many_to_one_same_topic`: multiple current daily topics map to one historical
  topic;
- `generic_operation_boundary_risk`: a same/subtopic decision appears to rely
  on a generic operation while objects differ;
- `platform_boundary_risk`: a bug relationship spans platforms without an
  explicit shared mechanism;
- `low_confidence_same_topic`: same-topic confidence is below the configured
  review threshold;
- `high_similarity_new_topic`: a new-topic decision rejects a high-similarity
  candidate and should be sampled for review;
- `low_information_media`: a held topic contains media evidence and should be
  visible in the appendix.

### 7.2 Reporting aggregation

Multiple daily topics may map to one stable topic. The audit records this as a
daily-boundary signal, while downstream reporting aggregates their deduplicated
conversation counts and evidence under the stable `topic_id`.

The experiment does not merge or delete daily topics.

## 8. Targeted Experiment

### 8.1 Sample construction

Build a deduplicated targeted set containing:

- all 22 current `uncertain` decisions;
- all 36 current `possible_subtopic` decisions;
- all low-confidence or low-similarity `same_topic` decisions;
- high-similarity `new_topic` decisions;
- every daily topic in a many-to-one same-topic group;
- deterministic high-precision low-information candidates.

Each row carries its old decision, current topic, recalled historical
candidates, member evidence, platform counts, and media indicator.

### 8.2 Model execution

Run the revised decision prompt through the two Knot routes using round-robin
assignment, four concurrent requests per route, checkpoint/resume, a 300-second
read timeout, and strict exact-coverage parsing.

The model must return exactly one of:

- `low_information`;
- `same_topic`;
- `new_topic`;
- `possible_subtopic`;
- `uncertain`.

### 8.3 Comparison output

Write an auditable comparison containing:

- old verdict and historical topic;
- new verdict and historical topic;
- changed/unchanged status;
- new eligibility status;
- audit flags;
- model confidence and reason;
- current and historical summaries;
- evidence link;
- blank human review columns.

## 9. Success Criteria

- Every targeted daily topic receives exactly one new decision.
- No unresolved call or parse failures remain.
- Every `low_information` topic is absent from the updated stable topic store
  and present in the low-information pool.
- No stable topic evidence is lost when a topic is held.
- The reviewed set contains no accepted false parent-child relationship based
  only on a generic shared operation.
- Platform handling follows the approved bug/request policy.
- Old and new decisions remain side by side; the experiment never overwrites
  the completed baseline run.
- All audit counts reconcile to the targeted sample count.

## 10. Outputs

Create a new isolated experiment directory containing:

- `targeted_topics.jsonl`;
- `revised_lifecycle_batches.jsonl`;
- `revised_lifecycle_model_rows.jsonl` plus checkpoints and failures;
- `revised_decisions.jsonl`;
- `low_information_pool.jsonl`;
- `boundary_audit.jsonl`;
- `comparison_summary.json`;
- `manifest.json` with hashes and no credentials;
- a review workbook with source feedback links;
- a concise Markdown result report.

## 11. Non-Goals

- Re-extracting conversation issue units;
- changing the 2026-07-12 or 2026-07-13 baseline artifacts;
- running a seven-day or thirty-day history;
- deciding final report-candidate quality;
- automatically merging or deleting stable topics;
- interpreting image or video contents.

## 12. Failure Handling

- Model calls use checkpoint/resume and retry only failed batches.
- A parse or exact-coverage failure blocks final comparison output.
- A low-information decision with a historical topic ID is rejected.
- A same/subtopic/uncertain decision must reference one recalled historical
  candidate.
- Credentials exist only in process environment variables and never in
  artifacts or manifests.
