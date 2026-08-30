# Caller-AI Topic Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI invoking `mining-feedback-topics` classify every selected topic candidate while the backend provides retrieval, durable partial progress, deterministic validation, verification, and export without invoking a semantic classifier.

**Architecture:** Protocol-v2 Runs freeze a caller-AI ownership contract and stop after retrieval at `classification_ready`. The Skill fetches immutable 20-item candidate pages and submits grounded decisions; the backend persists valid items independently, keeps invalid items pending, materializes a final caller-classification artifact at exact coverage, and verifies it without a review-override stage. Protocol-v1 verified Runs remain exportable, while unfinished v1 Runs are not migrated.

**Tech Stack:** Python 3.9+, FastAPI, SQLite, JSON/JSONL, pytest, standard-library Skill client

## Global Constraints

- Protocol v2 is exactly `version=2`, `owner=caller_ai`, page default/maximum `20`, matched evidence policy `exact_source_or_context_substring`, and `partial_acceptance=true`.
- New topic Runs must not read topic classifier configuration or invoke `default_classifier_route()`, `classify_candidates()`, `invoke_model_route()`, or any injected model callback.
- The Qwen embedding/vector service remains retrieval-only.
- Valid sibling decisions persist when one submitted decision is invalid; 500 candidates with one invalid decision becomes 499 accepted and 1 pending.
- Every matched evidence string is an exact substring of frozen source text or context; every not-matched decision has an empty evidence list.
- New status values are `classification_ready`, `classification_in_progress`, and `verification_ready`.
- Existing verified v1 Runs remain exportable; unfinished v1 Runs remain readable but cannot enter the v2 flow.
- New Run identity and idempotency include classification protocol version; v1 identities remain byte-for-byte unchanged.
- Do not repair or migrate experimental Runs `2e8ca1ba830b3a33` and `2849f3cba779da23`.
- Do not stage the user-owned untracked `WORKSPACE_GUIDE.md`; update its architecture description in place only after all code is verified.

---

### Task 1: Add the protocol-v2 contract and durable decision storage

**Files:**
- Create: `feedback_hub/topic_mining/protocol.py`
- Modify: `feedback_hub/topic_mining/run_store.py`
- Modify: `feedback_hub/tests/test_topic_mining_run_store.py`
- Create: `feedback_hub/tests/test_topic_mining_protocol.py`

**Interfaces:**
- Produces: `CLASSIFICATION_PROTOCOL_VERSION`, `CLASSIFICATION_OWNER`, `classification_capability()`, and `classification_protocol(run) -> tuple[int, str]`.
- Produces: new `classification_protocol_version` and `classification_owner` keyword arguments on the existing `TopicRunStore.create_or_get` method.
- Produces: `upsert_caller_decisions(run_id, candidate_set_sha256, decisions)`, `get_caller_decisions(run_id)`, and `caller_decision_progress(run_id, candidate_ids)`.

- [ ] **Step 1: Write failing protocol and migration tests**

Add tests that assert the exact capability object:

```python
def test_classification_capability_is_caller_ai_v2():
    assert classification_capability() == {
        "version": 2,
        "owner": "caller_ai",
        "candidate_page_default": 20,
        "candidate_page_maximum": 20,
        "matched_evidence": "exact_source_or_context_substring",
        "partial_acceptance": True,
    }
```

Add a migration test that creates the legacy `topic_run` schema and one v1 row, initializes `TopicRunStore`, and asserts:

```python
legacy = store.get("legacy-run")
assert legacy["classification_protocol_version"] == 1
assert legacy["classification_owner"] == "backend_model"
assert legacy["status"] == "review_ready"
```

Add identity and decision persistence tests:

```python
v1 = store.create_or_get(spec, 123)
v2 = store.create_or_get(
    spec, 123,
    classification_protocol_version=2,
    classification_owner="caller_ai",
)
assert v1["run_id"] != v2["run_id"]
assert store.create_or_get(spec, 123)["run_id"] == v1["run_id"]
assert store.create_or_get(
    spec, 123,
    classification_protocol_version=2,
    classification_owner="caller_ai",
)["run_id"] == v2["run_id"]

result = store.upsert_caller_decisions(
    v2["run_id"], "a" * 64,
    [{"item_id": "a", "label": "matched", "reason": "明确", "evidence": ["原文"]}],
)
assert result == {"inserted": ["a"], "updated": [], "unchanged": []}
assert store.upsert_caller_decisions(
    v2["run_id"], "a" * 64,
    [{"item_id": "a", "label": "matched", "reason": "明确", "evidence": ["原文"]}],
)["unchanged"] == ["a"]
assert store.upsert_caller_decisions(
    v2["run_id"], "a" * 64,
    [{"item_id": "a", "label": "not_matched", "reason": "修正", "evidence": []}],
)["updated"] == ["a"]
```

Assert audit revisions are 1 then 2, a mismatched candidate digest is rejected, and decision writes are rejected after status becomes `verification_ready`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_protocol.py \
  feedback_hub/tests/test_topic_mining_run_store.py \
  -k 'protocol or migration or caller_decision or identity' -q
```

Expected: imports and new store methods fail because protocol v2 and the decision tables do not exist.

- [ ] **Step 3: Implement the protocol authority**

Create `protocol.py`:

```python
from __future__ import annotations

from typing import Any, Mapping

CLASSIFICATION_PROTOCOL_VERSION = 2
CLASSIFICATION_OWNER = "caller_ai"
CANDIDATE_PAGE_LIMIT = 20


def classification_capability() -> dict[str, Any]:
    return {
        "version": CLASSIFICATION_PROTOCOL_VERSION,
        "owner": CLASSIFICATION_OWNER,
        "candidate_page_default": CANDIDATE_PAGE_LIMIT,
        "candidate_page_maximum": CANDIDATE_PAGE_LIMIT,
        "matched_evidence": "exact_source_or_context_substring",
        "partial_acceptance": True,
    }


def classification_protocol(run: Mapping[str, Any]) -> tuple[int, str]:
    version = run.get("classification_protocol_version", 1)
    owner = run.get("classification_owner", "backend_model")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("invalid classification protocol version")
    if not isinstance(owner, str) or not owner:
        raise ValueError("invalid classification owner")
    return version, owner
```

- [ ] **Step 4: Migrate the run schema without changing v1 identity**

Rebuild legacy `topic_run` tables whose SQL lacks the new statuses or protocol columns. The replacement table must:

```sql
status TEXT NOT NULL CHECK(status IN (
  'pending', 'running', 'classification_ready',
  'classification_in_progress', 'verification_ready',
  'review_ready', 'verified', 'failed', 'paused_quota_exhausted'
)),
classification_protocol_version INTEGER NOT NULL DEFAULT 1,
classification_owner TEXT NOT NULL DEFAULT 'backend_model',
UNIQUE(spec_hash, source_watermark_ms, classification_protocol_version)
```

Copy every existing column under one `BEGIN IMMEDIATE` transaction, supplying `1` and `backend_model` for old rows. Preserve claim/publication fields and all row values.

For v1, retain:

```python
run_id = sha256(f"{spec_hash}:{source_watermark_ms}")[:16]
```

For v2, use:

```python
run_id = sha256(
    f"{spec_hash}:{source_watermark_ms}:classification-v2"
)[:16]
```

- [ ] **Step 5: Add decision and audit tables**

Create:

```sql
CREATE TABLE IF NOT EXISTS topic_run_caller_decision (
  run_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  candidate_set_sha256 TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  revision INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY (run_id, item_id),
  FOREIGN KEY (run_id) REFERENCES topic_run(run_id)
);

CREATE TABLE IF NOT EXISTS topic_run_caller_decision_audit (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  candidate_set_sha256 TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  recorded_at_ms INTEGER NOT NULL,
  UNIQUE (run_id, item_id, revision)
);
```

`upsert_caller_decisions` must run under `BEGIN IMMEDIATE`, verify v2 caller ownership, accept only `classification_ready` or `classification_in_progress`, compare the frozen digest, classify writes as inserted/updated/unchanged, append audit revisions only for inserted/updated rows, and never delete sibling decisions.

- [ ] **Step 6: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_protocol.py \
  feedback_hub/tests/test_topic_mining_run_store.py -q
```

Expected: all protocol, migration, identity, decision, worker-claim, and publication tests pass.

- [ ] **Step 7: Commit**

```bash
git add feedback_hub/topic_mining/protocol.py \
  feedback_hub/topic_mining/run_store.py \
  feedback_hub/tests/test_topic_mining_protocol.py \
  feedback_hub/tests/test_topic_mining_run_store.py
git commit -m "feat: persist caller-ai topic decisions"
```

---

### Task 2: Stop v2 Runs after retrieval and expose stable candidate pages

**Files:**
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/topic_mining/api.py`
- Modify: `feedback_hub/tests/test_topic_mining_service.py`
- Modify: `feedback_hub/tests/test_topic_mining_api.py`

**Interfaces:**
- Consumes: protocol fields and v2 identity from Task 1.
- Produces: `get_candidate_page(run_id, offset, limit, store=None) -> dict[str, Any]`.
- Produces: v2 pipeline terminal worker state `classification_ready` with a frozen candidate-set digest.

- [ ] **Step 1: Write failing no-model and paging tests**

Add a service test with an injected model callback that fails if touched:

```python
def forbidden_model_call(*_args, **_kwargs):
    raise AssertionError("v2 topic run invoked a classifier")

run_topic_job(
    run_id, store=store, config=config,
    vector_client=fake_vector,
    model_call_fn=forbidden_model_call,
)
run = store.get(run_id)
manifest = json.loads(run["manifest_json"])
assert run["status"] == "classification_ready"
assert run["stage"] == "classification_ready"
assert manifest["classification_protocol"] == {
    "version": 2, "owner": "caller_ai",
}
assert len(manifest["candidate_set_sha256"]) == 64
assert not (Path(run["artifact_dir"]) / "classified.jsonl").exists()
assert not (Path(run["artifact_dir"]) / "classification_audit.jsonl").exists()
```

Add candidate-page assertions for 41 candidates:

```python
first = get_candidate_page(run_id, 0, 20, store=store)
second = get_candidate_page(run_id, 20, 20, store=store)
third = get_candidate_page(run_id, 40, 20, store=store)
assert [len(first["items"]), len(second["items"]), len(third["items"])] == [20, 20, 1]
assert [first["next_offset"], second["next_offset"], third["next_offset"]] == [20, 40, None]
assert first["candidate_set_sha256"] == second["candidate_set_sha256"]
assert all(item["decision_state"] == "pending" for item in first["items"])
assert first["topic"]["inclusion_criteria"] == list(spec.inclusion_criteria)
```

Assert limit 21, negative offsets, v1 Runs, missing artifacts, and digest mismatch are rejected.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_api.py \
  -k 'caller_ai or candidate_page or no_model' -q
```

Expected: the pipeline invokes classification and reaches `review_ready`; candidate-page interfaces do not exist.

- [ ] **Step 3: Freeze v2 protocol at Run creation**

In `create_run`, pass:

```python
classification_protocol_version=CLASSIFICATION_PROTOCOL_VERSION,
classification_owner=CLASSIFICATION_OWNER,
```

Add to the initial manifest:

```python
"manifest_version": 3,
"classification_protocol": {
    "version": CLASSIFICATION_PROTOCOL_VERSION,
    "owner": CLASSIFICATION_OWNER,
},
```

Do not add protocol fields to `TopicSpec`; they are execution identity, not user-controlled semantic scope.

Protocol-v1 manifests remain version 2. Stage validation and required-stage-chain checks must dispatch by frozen classification protocol so a v1 manifest is never interpreted with v2 stage rules.

- [ ] **Step 4: Stop the v2 worker after hybrid recall**

After loading selected recalls through `_classification_candidates`, branch on `classification_protocol(run)`. For v2 caller ownership:

```python
candidate_bytes = selected_path.read_bytes()
candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
manifest["candidate_set_sha256"] = candidate_digest
manifest["classification_owner"] = "caller_ai"
manifest["accepted_decision_count"] = 0
manifest["pending_decision_count"] = len(selected_recalls)
```

Materialize `classification_ready.json` containing protocol version, owner, candidate digest, and candidate count. Checkpoint a new `classification_ready` stage whose authenticated inputs include `selected_candidates.jsonl` and `item_contexts.json`; then set status/stage to `classification_ready` and return before any v1 classifier code.

Retain the existing classify/review path only when `classification_protocol_version == 1` and `classification_owner == backend_model`.

- [ ] **Step 5: Implement stable candidate paging**

`get_candidate_page` must:

1. require v2 caller ownership and status in `classification_ready`, `classification_in_progress`, or `verification_ready`;
2. verify the manifest and authenticated candidate/context artifacts;
3. load selected candidates in frozen order through `_classification_candidates`;
4. enforce `0 <= offset`, `1 <= limit <= 20`;
5. join each candidate to `_context_items(contexts, item_id)` and the store's accepted-decision IDs;
6. return `decision_state=accepted` without returning the accepted semantic decision, so paging does not bias a later re-read;
7. return the complete topic boundary and deterministic `next_offset`.

- [ ] **Step 6: Add the API endpoint**

Add:

```python
@router.get("/runs/{run_id}/candidate-page", dependencies=[Depends(require_token)])
def candidate_page(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=20),
) -> dict[str, Any]:
    try:
        return get_candidate_page(run_id, offset, limit, store=store)
    except RunVerificationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
```

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_api.py -q
```

Expected: v2 tests reach `classification_ready` without a model call, paging tests pass, and v1 fixture tests retain the legacy path.

- [ ] **Step 8: Commit**

```bash
git add feedback_hub/topic_mining/service.py feedback_hub/topic_mining/api.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_api.py
git commit -m "feat: expose caller-ai candidate pages"
```

---

### Task 3: Validate and partially accept caller-AI decisions

**Files:**
- Create: `feedback_hub/topic_mining/caller_classification.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/tests/test_topic_mining_service.py`
- Create: `feedback_hub/tests/test_topic_mining_caller_classification.py`

**Interfaces:**
- Produces: `validate_decision(raw, candidate, context_items) -> CallerDecision`.
- Produces: `submit_caller_classifications(run_id, decisions, store=None) -> dict[str, Any]`.
- Produces: item-specific stable codes `unknown_item`, `unsupported_fields`, `invalid_label`, `reason_required`, `evidence_required`, `evidence_not_allowed`, and `evidence_not_grounded`.

- [ ] **Step 1: Write failing validation tests**

Define the accepted shape:

```python
decision = validate_decision(
    {
        "item_id": "a", "label": "matched", "reason": "明确命中",
        "evidence": ["工具栏还在"],
    },
    {"item_id": "a", "text": "全屏以后工具栏还在"},
    [{"text": "补充上下文"}],
)
assert decision.to_dict() == {
    "item_id": "a", "label": "matched", "reason": "明确命中",
    "evidence": ["工具栏还在"],
}
```

Parameterize rejected inputs:

```python
[
    ({"item_id": "x", "label": "matched", "reason": "明确", "evidence": ["原文"]}, "unknown_item"),
    ({"item_id": "a", "label": "maybe", "reason": "明确", "evidence": []}, "invalid_label"),
    ({"item_id": "a", "label": "not_matched", "reason": "", "evidence": []}, "reason_required"),
    ({"item_id": "a", "label": "matched", "reason": "明确", "evidence": []}, "evidence_required"),
    ({"item_id": "a", "label": "matched", "reason": "明确", "evidence": ["概括而非原文"]}, "evidence_not_grounded"),
    ({"item_id": "a", "label": "not_matched", "reason": "排除", "evidence": ["文本"]}, "evidence_not_allowed"),
    ({"item_id": "a", "label": "not_matched", "reason": "排除", "evidence": [], "confidence": 0.9}, "unsupported_fields"),
]
```

- [ ] **Step 2: Write the 499/1 service regression test**

Build a v2 fixture with 500 frozen candidates and submit 500 decisions where item 317 uses ungrounded evidence. Assert:

```python
response = submit_caller_classifications(run_id, decisions, store=store)
assert len(response["accepted_ids"]) == 499
assert response["rejected"] == [{
    "item_id": "item-0317", "code": "evidence_not_grounded",
}]
assert response["accepted_count"] == 499
assert response["pending_count"] == 1
assert store.get(run_id)["status"] == "classification_in_progress"
assert len(store.get_caller_decisions(run_id)) == 499

fixed = submit_caller_classifications(run_id, [{
    "item_id": "item-0317", "label": "matched", "reason": "修正",
    "evidence": ["原文片段 317"],
}], store=store)
assert fixed["accepted_count"] == 500
assert fixed["pending_count"] == 0
assert store.get(run_id)["status"] == "verification_ready"
```

Assert no call to a model route and no sibling decision revision during repair.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_service.py \
  -k 'caller_decision or partial_acceptance or one_invalid' -q
```

Expected: caller decision validation and submission do not exist.

- [ ] **Step 4: Implement strict per-item validation**

Create an immutable `CallerDecision` dataclass with `to_dict()`. `validate_decision` must reject all fields outside `item_id`, `label`, `reason`, and `evidence`; compare item identity; strip only `reason`; preserve evidence bytes exactly; and search each evidence string in source plus context text.

Use a dedicated `DecisionValidationError(code)` so the service can return stable item errors without leaking source text.

- [ ] **Step 5: Implement partial acceptance and progress**

`submit_caller_classifications` must:

1. require v2 caller ownership and a mutable classification status;
2. authenticate the manifest, candidate set, and contexts;
3. reject duplicate request IDs individually without overwriting a sibling;
4. validate every raw item independently and collect valid decisions;
5. persist the valid subset through `upsert_caller_decisions`;
6. calculate accepted and pending IDs against the frozen candidate order;
7. update status to `classification_in_progress` when pending remains;
8. return accepted IDs, rejected entries, counts, and the first page offset containing a pending item.

Do not map a semantic validation error to terminal Run `failed`.

- [ ] **Step 6: Materialize exact coverage**

When pending reaches zero, sort effective decisions by frozen candidate order, serialize canonical JSONL, and checkpoint `caller_classifications.jsonl` as the `caller_classification` stage. Record:

```python
manifest.update({
    "classification_owner": "caller_ai",
    "classification_protocol_version": 2,
    "accepted_decision_count": len(candidate_ids),
    "pending_decision_count": 0,
    "classification_revision_count": store.caller_revision_count(run_id),
    "unresolved_classifier_items": 0,
    "unresolved_parser_items": 0,
})
```

Set status/stage to `verification_ready`. Never create `classified.jsonl`, `review_queue.jsonl`, or `review_overrides.jsonl` for v2.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_service.py -q
```

Expected: validation, partial acceptance, progress, correction, restart, idempotency, and exact-coverage materialization tests pass.

- [ ] **Step 8: Commit**

```bash
git add feedback_hub/topic_mining/caller_classification.py \
  feedback_hub/topic_mining/service.py \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_service.py
git commit -m "feat: accept caller-ai topic classifications"
```

---

### Task 4: Verify caller decisions and preserve v1 exports

**Files:**
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/topic_mining/export.py`
- Modify: `feedback_hub/tests/test_topic_mining_service.py`
- Modify: `feedback_hub/tests/test_topic_mining_export.py`

**Interfaces:**
- Consumes: authenticated `caller_classifications.jsonl` from Task 3.
- Produces: `verify_topic_run` protocol dispatch and unchanged final deliverables.

- [ ] **Step 1: Write failing v2 verification tests**

Add tests that assert:

```python
with pytest.raises(RunVerificationError, match="classification_incomplete"):
    verify_topic_run(classification_ready_run, store=store)

result = verify_topic_run(verification_ready_run, store=store)
assert result == {
    "run_id": run_id, "status": "verified", "matched_count": 2,
}
final = load_jsonl_objects(
    (artifact_dir / "final_reviewed.jsonl").read_bytes(),
)
assert all(row["source"] == "caller_ai" for row in final)
assert all(row["evidence"] for row in final)
```

Tamper caller decision evidence and its manifest hash, then assert verification still rejects `invalid_evidence`. Assert a valid not-matched decision has no exported row.

Add a legacy fixture with protocol version 1/status `verified` and assert both JSONL and XLSX re-export still work.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_export.py \
  -k 'caller_ai_verify or classification_incomplete or legacy_v1_export' -q
```

Expected: verification requires `review_ready` and v1 review artifacts for every Run.

- [ ] **Step 3: Dispatch verification by frozen protocol**

Keep the existing v1 implementation in `_verify_v1_topic_run`. Add `_verify_v2_topic_run` that:

- accepts only `verification_ready` or `verified`;
- returns `classification_incomplete` with no state mutation for earlier v2 statuses;
- authenticates snapshot, recall, selected candidates, contexts, and `caller_classifications.jsonl`;
- requires exact candidate coverage and manifest/store counts;
- reconstructs `ClassificationResult` values with `source="caller_ai"` only after schema validation;
- validates evidence, links, cutoff, and hard scope;
- builds matched final rows directly without review overrides;
- publishes `final_reviewed.jsonl` and the existing result-scope metadata.

`verify_topic_run` becomes:

```python
version, owner = classification_protocol(run)
if (version, owner) == (2, "caller_ai"):
    return _verify_v2_topic_run(run, store)
return _verify_v1_topic_run(run, store)
```

- [ ] **Step 4: Keep export presentation stable**

Do not change the nine-column workbook. Extend `quality_report.json` only with:

```json
{
  "classification_protocol_version": 2,
  "classification_owner": "caller_ai",
  "classification_revision_count": 501,
  "pending_decision_count": 0
}
```

For v1, omit these new keys or report the frozen legacy values without changing existing artifact validation.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_export.py -q
```

Expected: v2 verify/export and existing v1 export tests pass.

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/topic_mining/service.py feedback_hub/topic_mining/export.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_export.py
git commit -m "feat: verify caller-ai topic decisions"
```

---

### Task 5: Expose protocol v2 through the backend API

**Files:**
- Modify: `feedback_hub/topic_mining/api.py`
- Modify: `feedback_hub/tests/test_topic_mining_api.py`

**Interfaces:**
- Consumes: capability, candidate page, submission, and progress services.
- Produces: `/candidate-page`, `/classifications`, v2 public Run counts, and fail-closed v1 resume behavior.

- [ ] **Step 1: Write failing API contract tests**

Assert capabilities includes:

```python
assert payload["classification_protocol"] == {
    "version": 2,
    "owner": "caller_ai",
    "candidate_page_default": 20,
    "candidate_page_maximum": 20,
    "matched_evidence": "exact_source_or_context_substring",
    "partial_acceptance": True,
}
assert "classification_ready" in payload["run_statuses"]
assert "verification_ready" in payload["run_statuses"]
```

Test:

- `GET /candidate-page` returns at most 20 and stable `next_offset`;
- `POST /classifications` returns 200 with accepted/rejected siblings;
- `GET /runs/{id}` reports protocol owner/version and accepted/pending counts;
- v2 `resume` at classification states returns 409 `run_not_recoverable`;
- unfinished v1 candidate/classification calls return 409 `legacy_classification_protocol`;
- verify with pending items returns 409 `classification_incomplete`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_api.py \
  -k 'classification_protocol or candidate_page or classifications or classification_incomplete' -q
```

Expected: capability fields, statuses, endpoints, and progress fields are absent.

- [ ] **Step 3: Add capabilities and public status**

Import `classification_capability()` and add it to capabilities. Replace backend-classifier/review policy metadata for v2 with the caller-AI protocol while retaining candidate budgets.

Extend `_public_run` with:

```python
"classification_protocol_version": int(run.get("classification_protocol_version", 1)),
"classification_owner": str(run.get("classification_owner", "backend_model")),
"accepted_decision_count": progress["accepted_count"],
"pending_decision_count": progress["pending_count"],
```

Use store progress only for v2; retain legacy funnel/model fields for v1.

- [ ] **Step 4: Add decision submission endpoint**

```python
@router.post("/runs/{run_id}/classifications", dependencies=[Depends(require_token)])
def classifications(
    run_id: str, payload: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return submit_caller_classifications(run_id, payload, store=store)
    except RunVerificationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
```

Invalid individual decisions remain in the HTTP 200 `rejected` list. Use HTTP 422 only when the top-level body is not a list or exceeds the 20-item request limit.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_api.py -q
```

Expected: all v1/v2 API, concurrency, authorization, redaction, paging, and export tests pass.

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/topic_mining/api.py feedback_hub/tests/test_topic_mining_api.py
git commit -m "feat: serve caller-ai classification protocol"
```

---

### Task 6: Update the distributable Skill and local decision validator

**Files:**
- Create: `codex-skills/mining-feedback-topics/scripts/validate_topic_decisions.py`
- Modify: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py`
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Modify: `codex-skills/mining-feedback-topics/references/review-policy.md`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: protocol-v2 capabilities and API endpoints.
- Produces: `candidate-page` and `apply-classifications` client commands and fail-closed local validation.

- [ ] **Step 1: Write failing Skill protocol tests**

Add tests that:

- `prepare-spec` and `create-run` reject missing, v1, malformed, or backend-owned classification contracts;
- capabilities v2 is accepted exactly;
- `candidate-page RUN --output PAGE --offset 0 --limit 20` writes the returned page atomically;
- limits above 20 are rejected locally;
- `apply-classifications RUN --page PAGE --file DECISIONS` blocks missing/extra/duplicate IDs and ungrounded evidence without making an HTTP request;
- valid decisions POST to `/runs/RUN/classifications`;
- a 200 partial-acceptance response is preserved for the Agent to repair;
- Skill documentation no longer instructs v2 users to fetch `review-queue` or call `apply-overrides`;
- the body of `SKILL.md` remains within its existing 500-word budget.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py \
  -k 'classification_protocol or candidate_page or apply_classifications or decision' -q
```

Expected: client commands and decision validator are absent; old review workflow text remains.

- [ ] **Step 3: Implement strict capability parsing**

Add `_classification_contract(capabilities)` requiring exact values:

```python
expected = {
    "version": 2,
    "owner": "caller_ai",
    "candidate_page_default": 20,
    "candidate_page_maximum": 20,
    "matched_evidence": "exact_source_or_context_substring",
    "partial_acceptance": True,
}
```

Call it from `prepare-spec`, `create-run`, `candidate-page`, and `apply-classifications`. A mismatch is a local configuration blocker and no mutating request is sent.

- [ ] **Step 4: Create the local page-bound validator**

`validate_topic_decisions.py PAGE DECISIONS --output PREPARED [--repair]` must:

- parse only JSON objects produced by the candidate-page command and a JSON decision list;
- require exact page ID coverage for a normal page submission;
- in `--repair` mode, accept a non-empty subset only when every decision ID appears in the supplied page;
- reject unknown/duplicate IDs, unsupported fields/labels, empty reasons, and evidence violations;
- write canonical JSON to a separate output atomically and never overwrite either input.

The backend remains authoritative; local validation is an early error message, not a trust boundary.

- [ ] **Step 5: Add client commands**

Parser contract:

```text
candidate-page RUN_ID --output FILE --offset N --limit 20
apply-classifications RUN_ID --page PAGE --file DECISIONS [--repair]
```

`candidate-page` follows the endpoint query and writes raw response bytes atomically. `apply-classifications` invokes the validator into a temporary sibling file, posts its list, and removes the temporary file in `finally` without deleting caller inputs.

- [ ] **Step 6: Rewrite the Skill workflow**

The v2 Skill flow is:

1. capabilities and spec preparation;
2. create and poll until `classification_ready`;
3. fetch candidate pages in frozen order;
4. classify every item using source/context and exact evidence;
5. submit each page and repair only backend-rejected IDs;
6. confirm `verification_ready` with zero pending;
7. verify, export, and download.

State explicitly that the backend embedding model retrieves candidates but does not decide membership, and that the invoking AI is the sole semantic classifier. Remove v2 review-queue language from `SKILL.md`; retain legacy behavior only in the backend reference for historical diagnostics.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
```

Expected: all packaging, documentation, URL, schema, command, local-validation, redaction, and full client-flow tests pass.

- [ ] **Step 8: Commit**

```bash
git add codex-skills/mining-feedback-topics \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: classify topic candidates with caller ai"
```

---

### Task 7: Add end-to-end regression, operational docs, and deployment gates

**Files:**
- Modify: `feedback_hub/tests/test_topic_mining_end_to_end.py`
- Modify: `docs/devcloud-container-deployment.md`
- Modify in place but do not stage: `/Users/charvel/Desktop/用户反馈_2026_0612/WORKSPACE_GUIDE.md`

**Interfaces:**
- Consumes: complete protocol-v2 backend and Skill client.
- Produces: regression proof and a deployment/runbook contract.

- [ ] **Step 1: Write the failing end-to-end test**

Replace the fake backend-classifier fixture with a caller-AI flow:

```python
run = _create_v2_run(store, spec, source_watermark_ms)
run_topic_job(
    run["run_id"], store=store, config=config,
    vector_client=fake_vector,
    model_call_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("backend model called")
    ),
)
assert store.get(run["run_id"])["status"] == "classification_ready"

page = get_candidate_page(run["run_id"], 0, 20, store=store)
decisions = classify_fixture_page(page)
decisions[1]["evidence"] = ["not in source"]
partial = submit_caller_classifications(run["run_id"], decisions, store=store)
assert partial["pending_count"] == 1
submit_caller_classifications(
    run["run_id"], [corrected_decision(page["items"][1])], store=store,
)
assert store.get(run["run_id"])["status"] == "verification_ready"
assert verify_topic_run(run["run_id"], store=store)["status"] == "verified"
```

Assert source SHA is unchanged, platform hard scope remains canonical, vector-only paraphrases are classified by the caller AI, invalid evidence never becomes an accepted artifact, and the workbook has the current nine columns.

- [ ] **Step 2: Run the end-to-end test and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_end_to_end.py -q
```

Expected: the existing fixture follows backend classification/review and lacks v2 partial repair.

- [ ] **Step 3: Implement the v2 fixture and make it GREEN**

Use real service/store/API code with only source and vector providers faked. Do not inject a fake semantic backend model except the assertion-raising sentinel. Keep all fixture feedback synthetic.

Run the same command and expect all end-to-end tests to pass.

- [ ] **Step 4: Update deployment documentation**

Document:

- capabilities must report v2 caller ownership before a new Skill may run;
- `LLM_MODEL` is irrelevant to topic-mining v2 but can remain for other services;
- healthy Runs stop at `classification_ready` after recall;
- accepted/pending counts replace backend model/retry diagnostics;
- smoke validation deliberately submits one bad evidence item and confirms only that ID remains pending;
- old unfinished v1 Runs are not resumed.

Update the workspace guide's topic-mining architecture paragraph in place after preserving all existing user content. Do not stage the untracked file.

- [ ] **Step 5: Run the complete focused regression**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_protocol.py \
  feedback_hub/tests/test_topic_mining_run_store.py \
  feedback_hub/tests/test_topic_mining_contracts.py \
  feedback_hub/tests/test_topic_mining_source.py \
  feedback_hub/tests/test_topic_mining_retrieval.py \
  feedback_hub/tests/test_topic_mining_vector_client.py \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
python3 -m compileall -q feedback_hub/topic_mining \
  codex-skills/mining-feedback-topics/scripts
git diff --check
```

Expected: all focused tests and static checks pass. The known unrelated full-suite collection errors remain reported separately and are not changed by this work.

- [ ] **Step 6: Audit model-route removal**

Run:

```bash
rg -n "classify_candidates|default_classifier_route|LLM_MODEL|review-queue|apply-overrides" \
  feedback_hub/topic_mining codex-skills/mining-feedback-topics
```

Expected: model classifier/review references exist only in explicitly marked v1 compatibility code and legacy backend documentation; no v2 service or Skill path can reach them.

- [ ] **Step 7: Commit tracked changes**

```bash
git add feedback_hub/tests/test_topic_mining_end_to_end.py \
  docs/devcloud-container-deployment.md
git commit -m "test: verify caller-ai topic workflow"
```

Do not stage `WORKSPACE_GUIDE.md`.

- [ ] **Step 8: Deploy and smoke-test after explicit approval**

Run:

```bash
APP_PORT=8000 ./deploy_devcloud.sh
python3 codex-skills/mining-feedback-topics/scripts/topic_backend_client.py capabilities
```

Then create a synthetic/internal smoke Run, confirm `classification_ready`, classify at least two pages with one deliberate invalid evidence decision, confirm valid siblings persist and only one ID remains pending, correct it on the same Run, verify, export, and confirm a verified v1 artifact remains downloadable. Do not use either abandoned experimental Run.
