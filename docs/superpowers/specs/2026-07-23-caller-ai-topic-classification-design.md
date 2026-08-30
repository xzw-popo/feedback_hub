# Caller-AI Topic Classification Design

Date: 2026-07-23

## Goal

Move all semantic topic membership decisions from the Feedback Hub backend to the AI invoking `mining-feedback-topics`. The backend remains responsible for source integrity, hard scope, retrieval, durable progress, evidence validation, verification, and export, but it must not call `deepseek-v4-flash` or any other semantic classifier for new topic runs.

This removes the two observed backend-classifier failure modes:

- a model reply with incomplete or extra candidate IDs causing `classifier_unresolved`;
- a model-generated paraphrase being stored as evidence and later causing an unrepairable `invalid_evidence` failure outside the review queue.

Caller-AI output can still be malformed. The new protocol therefore validates and repairs individual decisions synchronously instead of poisoning an asynchronous Run.

## Responsibility Boundary

### Backend

The backend owns:

- source freshness, snapshots, coverage, and read-only enforcement;
- time/platform/product/channel/version hard scope;
- BM25 and vector retrieval, fusion, deduplication, and adaptive candidate budgets;
- immutable candidate ordering and bounded context construction;
- paged candidate delivery;
- durable, idempotent decision checkpoints;
- authoritative candidate-ID, label, evidence, URL, and scope validation;
- exact classification coverage, final verification, and XLSX/JSONL export.

The backend does not own semantic `matched` versus `not_matched` decisions for protocol-v2 Runs. It must not read `LLM_MODEL`, invoke `default_classifier_route()`, or schedule model jobs in this path. The Qwen embedding model remains a retrieval component and is not a classifier.

### Calling AI and Skill

The calling AI owns:

- translating the user request into the versioned topic specification;
- reading every selected candidate and its bounded context;
- deciding `matched` or `not_matched` against the inclusion and exclusion criteria;
- providing a concise reason;
- copying exact source or context substrings as evidence for every `matched` decision;
- repairing only the decisions rejected by deterministic validation;
- explaining result scope and known candidate-budget limits to the user.

The Skill owns orchestration and local deterministic checks. It does not embed a model, choose a backend model, query the source database directly, or silently drop rejected decisions.

## Protocol Version and Capability Handshake

Capabilities adds an authoritative contract:

```json
{
  "classification_protocol": {
    "version": 2,
    "owner": "caller_ai",
    "candidate_page_default": 20,
    "candidate_page_maximum": 20,
    "matched_evidence": "exact_source_or_context_substring",
    "partial_acceptance": true
  }
}
```

The updated Skill fails closed unless all fields match its supported protocol. This prevents a new Skill from accidentally creating a Run on a v1 backend that still delegates classification to DeepSeek.

Every new Run freezes `classification_protocol_version=2`, `classification_owner=caller_ai`, the selected candidate IDs in their stable order, and a candidate-set digest. Run identity and idempotency include the protocol version. Existing v1 identity is unchanged.

## Run State Machine

```text
pending
  -> snapshot
  -> hard_scope
  -> hybrid_recall
  -> classification_ready
  -> classification_in_progress
  -> verification_ready
  -> verified
```

`classification_ready` means the candidate set and contexts are frozen and no valid decisions have been accepted. The first accepted decision changes the status to `classification_in_progress`. Exact decision coverage changes it to `verification_ready`.

Network errors, process restarts, and invalid AI decisions do not change the candidate set or discard accepted decisions. There is no `classifier_unresolved` state for protocol v2. `verify` returns HTTP 409 `classification_incomplete` with counts when pending or rejected items remain; it does not report `invalid_evidence` for an item that has not yet been accepted.

## Candidate Paging

The backend exposes a stable page of the immutable selected set:

```text
GET /runs/{run_id}/candidate-page?offset=N&limit=20
```

The response contains:

- Run ID, protocol version, candidate-set digest, total, offset, and `next_offset`;
- the topic objective, inclusion criteria, exclusion criteria, labels, and hard scope;
- for each item: `item_id`, `source_item`, bounded `context_items`, and retrieval provenance;
- current decision state for the item: `pending` or `accepted`.

Paging follows the frozen order, so accepted decisions never shift offsets. The default and maximum page size are both 20 to bound prompt size. The caller follows `next_offset` and never infers offsets from total.

The Skill client command is:

```text
candidate-page RUN_ID --output FILE --offset OFFSET --limit 20
```

## Decision Submission

The AI produces one record per candidate it is deciding:

```json
{
  "item_id": "feedback-id",
  "label": "matched",
  "reason": "游戏全屏期间工具栏仍显示",
  "evidence": ["全屏以后工具栏还在"]
}
```

For `matched`, `evidence` is a non-empty string list and every string must be an exact substring of `source_item.text` or one supplied `context_items` text. For `not_matched`, `evidence` must be an empty list. `item_id`, `label`, `reason`, and `evidence` are the only caller-supplied fields; backend provenance supplies the caller identity, timestamps, and candidate-set digest.

The Skill first validates a page against the downloaded candidate file. It rejects missing, duplicate, extra, invalid-label, empty-reason, and ungrounded-evidence decisions before making a request.

The backend endpoint is:

```text
POST /runs/{run_id}/classifications
```

The backend validates each submitted item independently against hash-verified candidates and contexts. It returns HTTP 200 with:

```json
{
  "accepted_ids": ["a", "b"],
  "rejected": [
    {"item_id": "c", "code": "evidence_not_grounded"}
  ],
  "accepted_count": 102,
  "pending_count": 398,
  "next_pending_offset": 100
}
```

Valid decisions are committed even when another decision in the request is rejected. Resubmitting an identical accepted decision is idempotent. Before `verification_ready`, a corrected decision for the same item replaces the previous decision through an audited revision; after `verification_ready`, decisions are immutable unless the Run is explicitly reopened by a future protocol.

The Skill command is:

```text
apply-classifications RUN_ID --page CANDIDATE_PAGE --file DECISIONS
```

The caller repairs only entries listed in `rejected`. It never reclassifies already accepted pages merely because one item failed.

## Durable Storage and Finalization

Accepted caller decisions are stored transactionally under a uniqueness key of `(run_id, item_id)` and bound to the frozen candidate-set digest. Each replacement retains an audit revision without modifying source or retrieval artifacts.

When all selected candidate IDs have one accepted decision, the backend atomically materializes `caller_classifications.jsonl`, records its digest, and advances to `verification_ready`. A restart can reconstruct progress from accepted decisions; it never needs a model checkpoint or `classification_audit.jsonl` for protocol v2.

Verification checks:

- exact coverage of the frozen selected candidate set;
- one effective decision per ID and no extra IDs;
- labels are exactly `matched` or `not_matched`;
- every matched decision has grounded evidence;
- every rejected decision has no evidence;
- source links, data cutoff, scope, snapshot identity, vector watermark, and artifact hashes remain valid.

The existing final XLSX/JSONL presentation contract remains unchanged. `quality_report.json` records `classification_owner=caller_ai`, protocol version, accepted decision count, correction count, and zero pending decisions.

## Removal of the Review-Override Stage

Protocol v2 has no backend semantic baseline to override, so `review-queue` and `apply-overrides` are not part of its successful path. The calling AI's classification is the decision layer. Backend verification remains independent and deterministic.

The v1 commands remain available only for compatible historical Runs. The Skill chooses commands from the Run's frozen protocol and never applies v1 review commands to v2 Runs.

## Candidate Budgets and Cost

Existing adaptive candidate selection remains backend-owned. A standard Run still selects according to the elapsed-day budget and maximum 500. A 500-item Run requires 25 candidate pages. Exhaustive mode retains its safety limit and must disclose that it can require substantially more AI time.

The backend reports retrieved, selected, accepted, pending, and matched counts. Representative versus reviewed result scope continues to depend on whether retrieval exceeded the selected candidate set, not on which component performed classification.

## Compatibility and Migration

- Existing verified v1 Runs remain downloadable and re-exportable.
- Existing unfinished v1 experimental Runs are readable but are not migrated or resumed into v2.
- New Runs are v2 only after deployment.
- The backend may retain v1 classifier modules for historical tests and verified-artifact compatibility, but the v2 service path must prove that no model route is invoked.
- `LLM_API_URL`, `LLM_API_KEY`, and `LLM_MODEL` may remain for unrelated Feedback Hub features; topic-mining v2 does not consume them.

The experimental Runs `2e8ca1ba830b3a33` and `2849f3cba779da23` are not repaired. They serve as regression fixtures for protocol design only; real feedback text is not committed.

## Failure Handling

- Missing or incompatible classification capability: Skill stops before creating a Run.
- Candidate page hash or protocol mismatch: request is rejected without accepting decisions.
- Missing/extra/duplicate IDs in a local page: local validation blocks submission.
- One invalid backend decision: valid siblings persist; only that ID remains pending.
- Agent interruption: restart from backend accepted/pending counts and stable pages.
- Verify before complete coverage: HTTP 409 `classification_incomplete`, never a terminal failed Run.
- Evidence rejected after submission: return an item-specific code without exposing unrelated data.
- Source or vector integrity failure: keep the existing terminal integrity gates; caller classification cannot override them.

## Testing Strategy

The implementation must include tests for:

1. capabilities advertises protocol v2 and the Skill fails closed on v1 or malformed contracts;
2. creating a v2 Run reaches `classification_ready` without calling any model route, even when `LLM_MODEL=deepseek-v4-flash` is configured;
3. candidate pages are stable, bounded at 20, complete, and context-grounded;
4. local validation rejects missing, extra, duplicate, invalid-label, and ungrounded-evidence decisions;
5. backend partial acceptance persists valid siblings and returns item-specific rejection codes;
6. 500 candidates with one invalid decision produces `accepted_count=499`, `pending_count=1`, and a repairable Run rather than `0 classified`, `classifier_unresolved`, or terminal `invalid_evidence`;
7. correcting the one rejected item advances the same Run to `verification_ready` without reprocessing the other 499;
8. restart and repeated submissions preserve accepted progress and idempotency;
9. verify rejects incomplete coverage and succeeds with exact grounded coverage;
10. XLSX/JSONL export remains unchanged apart from v2 provenance in machine-readable quality metadata;
11. verified v1 Runs remain exportable while unfinished v1 Runs are not resumable through v2;
12. an end-to-end Skill fixture classifies paged candidates, repairs one rejected evidence decision, verifies, and exports without a backend classifier call.

## Deployment

Deploy backend and Skill together. After restart:

1. confirm capabilities reports `classification_protocol.version=2` and `owner=caller_ai`;
2. create a smoke Run and confirm it stops at `classification_ready` with zero model provenance;
3. classify at least two pages through the Skill client, including one deliberately invalid evidence decision;
4. confirm valid decisions persist, the invalid ID alone remains pending, and its correction resumes the same Run;
5. verify and export;
6. confirm existing verified v1 artifacts remain downloadable.
