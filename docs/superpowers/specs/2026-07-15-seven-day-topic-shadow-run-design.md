# Seven-Day Topic Shadow Run Design

**Date:** 2026-07-15

**Status:** Approved for implementation

## 1. Purpose

Run the dynamic topic discovery and topic-memory lifecycle chain sequentially
for 2026-07-08 through 2026-07-14, using 2026-07-07 only as a hidden warm-up
day. The run validates whether bottom-up topics can remain coherent across
multiple days before the project starts designing the final daily or weekly
report.

This iteration answers four questions:

1. Can complete daily feedback be converted into auditable conversation-level
   issue units without losing media and source-link evidence?
2. Can daily topics be discovered without relying on a fixed topic taxonomy?
3. Can the topic store distinguish continuing, new, possible-subtopic,
   uncertain, and low-information feedback across seven sequential days?
4. Can the model-call layer detect Knot quota exhaustion, pause safely, and
   preserve exact output coverage and route provenance for later resumption?

The shadow run does not decide the final report layout or claim that every
eligible topic belongs in a report.

## 2. Selected Approach

Use a sequential cumulative topic memory:

```text
2026-07-07 warm-up
  -> filtered conversation extraction
  -> daily topic discovery
  -> first-day memory eligibility
  -> hidden topic store

2026-07-08 report day
  -> consumes 2026-07-07 topic store
  -> extracts, discovers, recalls, decides, audits, updates memory
  -> emits shadow-day outputs

2026-07-09 through 2026-07-14
  -> each day consumes the preceding day's updated topic store
```

This is preferred over two alternatives:

- **Independent daily runs followed by retrospective merging:** easier to run,
  but it does not test the actual memory decisions made each day.
- **One global eight-day reclustering:** may create cleaner retrospective
  clusters, but rewrites historical identity and cannot simulate daily use.

The sequential approach preserves the information that would have been
available on each day and makes every topic transition auditable.

## 3. Preconditions

### 3.1 High-risk boundary review

Before pulling the seven-day run, create one review workbook containing:

- all 39 unique high-risk rows already identified in the 2026-07-12 to
  2026-07-13 refinement result;
- 20 deterministically stratified rows from the 50
  `high_similarity_new_topic` rows, covering similarity bands, platforms,
  feedback types, and media presence.

Codex fills the human-result and human-reason columns for clear cases. Rows
whose boundary remains genuinely ambiguous stay blank for user review. The
review does not silently rewrite the completed baseline artifacts. The shadow
run may proceed with genuinely ambiguous rows only when the review finds no
systematic prompt or boundary-rule defect; unresolved rows remain explicit
audit items.

### 3.2 Self-owned API calibration

Run a 20-row canary through the self-owned OpenAI-compatible API. Select rows
from already reviewed decisions and include all five lifecycle verdicts,
multiple platforms, both media and non-media cases, and both candidate-bearing
and candidate-free cases. Include at least two rows for each verdict and at
least five reviewed rows whose verdict references a historical topic.

The self-owned API is eligible for a future full lifecycle fallback only when:

- exact lifecycle-verdict agreement is at least 17 of 20 rows; and
- among rows whose reviewed verdict references history, exact
  `historical_topic_id` agreement is at least 80%.

The canary is calibration only in this shadow run. Passing the thresholds does
not authorize automatic fallback. If either threshold fails, record the
quality result and do not use the self-owned API for shadow-run traffic. The
canary uses the same task prompt, schema, and strict parser as the shadow run.

## 4. Input Data

Pull a fresh, complete local input for every date from 2026-07-07 through
2026-07-14. Do not reuse partial experiment exports merely because they already
exist.

Each daily input must preserve:

- conversation ID and feedback/message ID;
- event time and source date;
- user-authored message text in chronological order;
- platform, app version, device, and channel metadata when present;
- source feedback URL;
- deterministic `has_media` evidence derived from raw attachment/media fields.

Customer-service or agent-authored replies are retained only as bounded
conversation context when the existing role metadata identifies them. They are
never treated as user evidence, never counted as issue units, and never become
report links by themselves. Rows with unknown author role are surfaced in the
input summary rather than guessed.

Every daily pull must pass a reconciliation gate before model calls start:

- raw message count equals prepared input count plus explicitly excluded rows;
- every prepared conversation has a stable conversation ID;
- source links and media indicators have nonnegative counts;
- excluded role counts are reported by reason;
- no duplicate feedback ID occurs within one daily input.

## 5. Daily Processing Flow

### 5.1 Conversation issue extraction

The model receives one bounded conversation at a time and returns zero or more
semantic issue units. A conversation may contain multiple independently
actionable issues, and each issue unit retains its supporting feedback IDs,
text spans, links, platform, feature candidate, feedback type, and media
indicator.

The extraction stage may use context to resolve pronouns or short follow-ups,
but it cannot infer the content of an image or video. Media with insufficient
text becomes a `media_context_only` unit or remains visible through the media
appendix path.

### 5.2 Daily open topic discovery

Flatten product-relevant semantic issue units, embed their factual summaries,
and use vector similarity only to recall manageable comparison buckets. The
model decides the actual within-day grouping. Singleton topics remain valid.

A topic boundary represents one independently actionable investigation, fix,
or product decision. Shared platform, feature, sentiment, or generic operation
does not by itself justify merging two issue units.

### 5.3 Memory eligibility and lifecycle decision

For every daily topic, recall up to the configured number of similar historical
topics and ask the model for exactly one verdict:

- `same_topic`;
- `new_topic`;
- `possible_subtopic`;
- `uncertain`;
- `low_information`.

Similarity recalls candidates only. It never determines the verdict.

On 2026-07-07 the historical store is empty, but the eligibility decision still
runs. The model may return only `new_topic` or `low_information` for that day.
The system must not seed every discovered topic directly into memory.

`low_information` retains all evidence in the low-information pool, does not
receive a stable topic ID, and enters the media appendix when media evidence is
present. Other valid verdicts update or create stable topic memory according to
the existing lifecycle rules. `uncertain` remains reviewable and does not
silently merge topics.

### 5.4 Boundary audit and memory update

After lifecycle decisions, run deterministic boundary audits for:

- many daily topics mapping to one historical topic;
- generic-operation boundary risk;
- cross-platform bug boundary risk;
- low-confidence same-topic decisions;
- high-similarity new-topic decisions;
- low-information topics with media evidence.

The audit also recalls prior held low-information rows against newly eligible
topics. A high-similarity pair emits `possible_low_information_upgrade`; it
does not merge, delete, or claim that the earlier feedback has been resolved
into the new topic without a later reviewed decision.

Audits emit flags and do not rewrite model decisions. Only after exact coverage,
parser invariants, and audit generation succeed does the day write its stable
topic store for the next day. A failed day blocks every later day.

## 6. Model Call Architecture

### 6.1 Agent prompts

Keep the existing generic system prompt on both Knot agents unchanged. Earlier
experiments did not show a taxonomy or reasoning problem that requires a system
prompt change. Task-specific definitions, candidate data, and output contracts
remain in the per-request prompt.

Revise lifecycle request formatting to:

- remove Markdown fences from the output example;
- explicitly require one legal JSON object and exact ID coverage;
- forbid literal ASCII double quotes inside free-text `reason` values and use
  Chinese corner quotes `「」` when quotation is needed;
- keep reasons concise and prohibit invented IDs or facts.

Conversation extraction and daily clustering retain their own schemas and
strict parsers.

### 6.2 Route order

The call layer exposes three logical routes:

1. Knot agent A, maximum four concurrent calls;
2. Knot agent B, maximum four concurrent calls;
3. the self-owned OpenAI-compatible API, calibrated for a possible later
   fallback but disabled for automatic shadow-run traffic.

New work is balanced across currently available Knot routes. A route is marked
quota-exhausted for the remainder of the run only after an explicit quota/rate
limit response recognized by the adapter. On the first explicit quota signal
from either Knot route, stop scheduling new work across all routes, allow only
already in-flight requests to finish, persist a `paused_quota_exhausted` run
state, and return control to the user. Do not continue on the other Knot route
and do not send shadow-run work to the self-owned API without new user
approval.

### 6.3 Retry classification

- Timeouts, connection failures, and HTTP 5xx responses retry on the original
  route with bounded exponential backoff.
- An explicit Knot quota response is not retried and triggers the global pause
  described above.
- A parse or exact-coverage failure is not treated as quota exhaustion.
- Lifecycle batches contain at most five topics. A failed parse retries once;
  if it still fails, the batch is split into batches of at most three topics.
- The parser never guesses malformed JSON and never creates an
  `irrelevant_invalid` fallback for a failed lifecycle response.
- An unrecognized permanent call error is checkpointed and blocks downstream
  finalization until resolved or explicitly rerun.

### 6.4 Provenance and credentials

Every model result records:

- logical route source;
- endpoint class (`knot_agent` or `openai_compatible`), never its credential;
- configured model name or `agent_default`;
- prompt version;
- attempt count and retry chain;
- elapsed time, parse status, and terminal error class.

URLs, tokens, and API keys are read only from environment variables. They must
not appear in prompts, checkpoints, manifests, workbooks, Markdown reports, or
git history.

## 7. Outputs

Write each day to an isolated directory and preserve prior experiment outputs.
For 2026-07-08 through 2026-07-14, produce:

- input summary and reconciliation counts;
- conversation packs and conversation issue units;
- daily similarity candidates and daily topics;
- lifecycle candidates, model decisions, and route provenance;
- topic events and the updated stable topic store;
- low-information pool;
- media appendix;
- boundary audit;
- daily machine-readable summary and concise shadow report.

2026-07-07 produces the same machine-readable artifacts but is marked
`warm_up_only=true` and is excluded from all seven-day report counts.

After 2026-07-14, produce a seven-day summary containing:

- daily input, conversation, issue-unit, and topic counts;
- new, continuing, possible-subtopic, uncertain, and low-information counts;
- topic continuations and reappearances across days;
- possible low-information upgrade pairs recalled by later evidence;
- daily and total media-appendix counts;
- boundary-audit counts and unresolved review rows;
- route share, retries, parse failures, quota transitions, and self-API usage;
- a review workbook with clickable source feedback links.

The Markdown shadow reports are diagnostic artifacts, not the final product
daily report.

## 8. Failure and Resume Semantics

- Every model stage writes append-only checkpoints keyed by stable work-item ID.
- Resume skips only rows that previously passed parsing and schema validation.
- A rerun with a different prompt version writes a new output namespace rather
  than mixing incompatible decisions in one checkpoint.
- Daily finalization requires exact coverage of all expected conversations,
  clustering buckets, and lifecycle topics.
- If a day cannot finalize, later dates do not start because their historical
  memory would be invalid.
- If either Knot route has an unresolved non-quota call failure after bounded
  retries, leave the checkpoint resumable and stop the shadow run without
  fabricating labels or switching to the self-owned API.

## 9. Verification and Acceptance Criteria

### 9.1 Deterministic tests

- first-day empty history still invokes memory eligibility;
- first-day `low_information` topics receive no stable topic ID;
- user evidence excludes customer-service-authored messages;
- media and source links survive every stage;
- quota classification and route provenance are deterministic;
- the first explicit Knot quota response stops new scheduling and writes a
  resumable paused state;
- transient errors remain on their original route;
- malformed JSON causes retry and batch splitting, not semantic fallback;
- checkpoint resume never duplicates a completed work item;
- a failed day cannot update memory or start the following day;
- credentials never appear in persisted artifacts.

### 9.2 Shadow-run acceptance

- all eight daily inputs pass reconciliation;
- 2026-07-07 is used as memory but excluded from report-period counts;
- every report-period conversation is either successfully extracted or appears
  in an explicit unresolved-failure artifact;
- every discovered daily topic has exactly one valid lifecycle verdict;
- every low-information topic is absent from stable memory and present in the
  low-information pool;
- all media-bearing low-information feedback appears in the media appendix;
- all seven report days finalize sequentially with reproducible manifests;
- route and retry totals reconcile to persisted model rows;
- the seven-day workbook contains working source links and no credentials.

The run may complete with boundary-audit or human-review rows. Those are
quality findings, not pipeline failures, provided no schema or coverage failure
is hidden.

## 10. Non-Goals

- Publishing a production daily or weekly report;
- deciding the final report-candidate ranking or importance model;
- reading or semantically interpreting image or video contents;
- creating a fixed, manually maintained global topic taxonomy;
- globally reclustering all eight days after the sequential run;
- rewriting completed 2026-07-12 to 2026-07-13 experiment artifacts;
- changing the two Knot agents' system prompt;
- deploying the shadow pipeline as a scheduled production job.

## 11. Expected Scale

Based on the completed 2026-07-12 and 2026-07-13 experiments, a full replay is
expected to require roughly 6,000 to 7,000 model requests, dominated by
conversation extraction. The two Knot agents may therefore exhaust their
nominal daily quota during a same-day historical replay. This is expected; the
first explicit quota signal pauses this run so the user can decide whether to
wait, resume on remaining Knot capacity, or authorize the calibrated self-owned
API.

The implementation must report actual request counts and route share rather
than treating this estimate as a limit.
