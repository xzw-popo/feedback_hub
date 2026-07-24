# Item-Local Topic Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade caller-AI topic mining to protocol v3 so every exported feedback item is independently supported by exact evidence from its own text.

**Architecture:** Protocol v3 becomes part of Run identity and the only mutable caller-AI protocol. Context remains in candidate pages for interpretation, while local validation, backend submission, verification, and final-row validation all enforce candidate-only evidence. Verified v1/v2 artifacts remain readable and exportable, but nonterminal v2 Runs cannot continue.

**Tech Stack:** Python 3, FastAPI, SQLite, pytest, Markdown Skill instructions, standard-library CLI.

## Global Constraints

- New Runs use caller-AI protocol version `3`.
- Capabilities advertise `matched_evidence=exact_candidate_substring`.
- `matched` evidence must be an exact non-empty substring of `item.text`.
- `context_items` remain visible but cannot establish membership.
- Context-only evidence is rejected with `evidence_not_candidate_grounded`.
- Valid sibling decisions remain accepted when one decision is rejected.
- Protocol version remains part of Run identity.
- Verified v1/v2 artifacts remain readable/exportable; nonterminal v2 Runs reject paging, submission, and verification.
- Backend and Skill must deploy together.

---

### Task 1: Protocol v3 Identity and Persistence

**Files:**
- Modify: `feedback_hub/topic_mining/protocol.py`
- Modify: `feedback_hub/topic_mining/run_store.py`
- Modify: `feedback_hub/topic_mining/api.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/topic_mining/export.py`
- Test: `feedback_hub/tests/test_topic_mining_run_store.py`
- Test: `feedback_hub/tests/test_topic_mining_api.py`
- Test: `feedback_hub/tests/test_topic_mining_service.py`

**Interfaces:**
- Produces: `CLASSIFICATION_PROTOCOL_VERSION = 3`.
- Produces: capability field `matched_evidence = "exact_candidate_substring"`.
- Produces: Run IDs ending logically in the identity component `classification-v3`.
- Consumes: v3 caller decisions only in `TopicRunStore.upsert_caller_decisions`.

- [ ] **Step 1: Write failing protocol and Run identity tests**

Update capability assertions to require v3 and add:

```python
def test_v3_run_identity_is_distinct_from_v1_and_v2(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    v2 = store.create_or_get(
        valid_topic_spec, 1234,
        classification_protocol_version=2,
        classification_owner="caller_ai",
    )
    v3 = store.create_or_get(
        valid_topic_spec, 1234,
        classification_protocol_version=3,
        classification_owner="caller_ai",
    )
    same_v3 = store.create_or_get(
        valid_topic_spec, 1234,
        classification_protocol_version=3,
        classification_owner="caller_ai",
    )
    assert v3["run_id"] != v2["run_id"]
    assert v3["run_id"] == same_v3["run_id"]
    assert v3["run_id"] == hashlib.sha256(
        f"{v3['spec_hash']}:1234:classification-v3".encode()
    ).hexdigest()[:16]
```

Change caller-decision persistence tests to create v3 Runs. Add a v2 nonterminal test that expects `caller decisions require classification protocol v3`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_run_store.py \
  feedback_hub/tests/test_topic_mining_api.py \
  -k "protocol or capabilities or caller_decisions or create_run" -q
```

Expected: FAIL because capabilities and storage support only v2.

- [ ] **Step 3: Implement v3 protocol and identity**

In `protocol.py`:

```python
CLASSIFICATION_PROTOCOL_VERSION = 3
CLASSIFICATION_OWNER = "caller_ai"

def classification_capability() -> dict[str, Any]:
    return {
        "version": 3,
        "owner": "caller_ai",
        "candidate_page_default": 20,
        "candidate_page_maximum": 20,
        "matched_evidence": "exact_candidate_substring",
        "partial_acceptance": True,
    }
```

In `run_store.py`, allow both versions to be represented but only v3 to mutate:

```python
expected_owner = {
    1: "backend_model",
    2: "caller_ai",
    3: "caller_ai",
}.get(classification_protocol_version)
```

Change `upsert_caller_decisions` to require version 3 and update its stable error text.

Replace new-run and active caller-AI checks in `api.py`, `service.py`, and `export.py` with the protocol constants or exact `(3, "caller_ai")`. Keep explicit historical handling for verified v2 Runs. Compute caller Run identity generically:

```python
if version > 1 and owner == "caller_ai":
    identity += f":classification-v{version}"
```

Expose pending counts for v3, not v2. Ensure manifest caller-AI stage detection accepts v3 for new work and recognizes v2 only for immutable verified artifact reads.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command plus:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_service.py \
  -k "caller_ai_run_stops or candidate_page" -q
```

Expected: all selected tests PASS with v3 assertions.

- [ ] **Step 5: Commit protocol v3**

```bash
git add feedback_hub/topic_mining/protocol.py feedback_hub/topic_mining/run_store.py feedback_hub/topic_mining/api.py feedback_hub/topic_mining/service.py feedback_hub/topic_mining/export.py feedback_hub/tests/test_topic_mining_run_store.py feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_topic_mining_service.py
git commit -m "feat: introduce caller-ai topic protocol v3"
```

### Task 2: Candidate-Only Decision Validation

**Files:**
- Modify: `feedback_hub/topic_mining/caller_classification.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `codex-skills/mining-feedback-topics/scripts/validate_topic_decisions.py`
- Test: `feedback_hub/tests/test_topic_mining_caller_classification.py`
- Test: `feedback_hub/tests/test_topic_mining_service.py`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Produces: `validate_decision(..., evidence_scope="candidate")`.
- Supports: `evidence_scope="candidate_or_context"` only for immutable v2 verification compatibility.
- Produces: item rejection code `evidence_not_candidate_grounded`.

- [ ] **Step 1: Write failing context-only regression tests**

Add:

```python
def test_context_only_evidence_cannot_match_candidate():
    with pytest.raises(DecisionValidationError) as error:
        validate_decision(
            {
                "item_id": "a",
                "label": "matched",
                "reason": "上下文相关",
                "evidence": ["语音输入无法识别"],
            },
            {"item_id": "a", "text": "今天天气不错"},
            [{"text": "语音输入无法识别"}],
        )
    assert error.value.code == "evidence_not_candidate_grounded"
```

Add a passing test where candidate evidence matches while unrelated context is present. Add a mixed submission test proving valid siblings persist and only the context-only item is rejected.

For the bundled validator, add a page whose `item.text` is unrelated and `context_items` contain the evidence; expect local exit code `2` and `decision evidence is not candidate-grounded`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py \
  -k "context_only or candidate_grounded" -q
```

Expected: FAIL because both validators accept context-only evidence.

- [ ] **Step 3: Implement item-local validation**

In `caller_classification.py`:

```python
def validate_decision(
    raw: Mapping[str, Any],
    candidate: Mapping[str, Any],
    context_items: Sequence[Mapping[str, Any]],
    *,
    evidence_scope: str = "candidate",
) -> CallerDecision:
    ...
    candidate_text = candidate.get("text")
    context_texts = [
        text for item in context_items
        if isinstance(item, Mapping)
        for text in [item.get("text") or item.get("feedback_text")]
        if isinstance(text, str)
    ]
    if label == "matched":
        for fragment in evidence:
            if isinstance(candidate_text, str) and fragment in candidate_text:
                continue
            if (
                evidence_scope == "candidate"
                and any(fragment in text for text in context_texts)
            ):
                raise DecisionValidationError(
                    "evidence_not_candidate_grounded"
                )
            if (
                evidence_scope == "candidate_or_context"
                and any(fragment in text for text in context_texts)
            ):
                continue
            raise DecisionValidationError("evidence_not_grounded")
```

Reject unsupported `evidence_scope` values. Submission uses the default candidate scope. Historical v2 verification explicitly passes `candidate_or_context`.

In `validate_topic_decisions.py`, build authoritative texts only from `row["item"]["text"]`; inspect context solely to choose the more specific context-only error message. Keep `not_matched` behavior unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command, then the complete caller-classification test module.

Expected: all tests PASS and mixed-page partial acceptance remains intact.

- [ ] **Step 5: Commit item-local validators**

```bash
git add feedback_hub/topic_mining/caller_classification.py feedback_hub/topic_mining/service.py codex-skills/mining-feedback-topics/scripts/validate_topic_decisions.py feedback_hub/tests/test_topic_mining_caller_classification.py feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "fix: require candidate-local topic evidence"
```

### Task 3: Verification, Historical Compatibility, and End-to-End Export

**Files:**
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/topic_mining/export.py`
- Test: `feedback_hub/tests/test_topic_mining_service.py`
- Test: `feedback_hub/tests/test_topic_mining_end_to_end.py`
- Test: `feedback_hub/tests/test_topic_mining_export.py`
- Test: `feedback_hub/tests/test_topic_mining_api.py`

**Interfaces:**
- Produces: v3 verification that validates final evidence against `source_item.text` only.
- Preserves: verified v2 artifact download/export without mutation.
- Rejects: nonterminal v2 paging, submission, and verification as `legacy_classification_protocol`.

- [ ] **Step 1: Write failing Verify and compatibility tests**

Add a v3 verification fixture with candidate text “今天天气不错”, context text “语音输入无法识别”, and a forged persisted matched decision citing only the context. Expect `RunVerificationError("invalid_evidence")`.

Add:

```python
def test_final_rows_reject_context_only_evidence_for_v3(...):
    with pytest.raises(RunVerificationError, match="invalid_evidence"):
        _validate_final_rows(
            [context_only_row],
            spec,
            contexts={"a": [{"text": "语音输入无法识别"}]},
            expected_run_id=run_id,
            expected_data_cutoff_ms=cutoff,
            evidence_scope="candidate",
        )
```

Create a verified v2 artifact fixture and assert the artifact endpoint and `export_topic_run` still work. Create a nonterminal v2 Run and assert candidate page, submission, and Verify return the legacy protocol blocker.

Update the end-to-end fixture to include an unrelated candidate and a related same-conversation context. First submit it as context-only `matched`, expect rejection, then repair it to `not_matched`. Assert the final workbook excludes that candidate and every final row has only `source_text` evidence.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_api.py \
  -k "context_only or historical_v2 or caller_ai_end_to_end" -q
```

Expected: FAIL because Verify still accepts context and the end-to-end output includes context-backed candidates.

- [ ] **Step 3: Implement protocol-aware verification**

Dispatch explicitly:

```python
def verify_topic_run(run_id: str, *, store=None) -> dict[str, Any]:
    store = store or default_store()
    run = _require_run(run_id, store)
    protocol = classification_protocol(run)
    if protocol == (3, "caller_ai"):
        return _verify_caller_ai_topic_run(
            run, store, evidence_scope="candidate",
        )
    if protocol == (2, "caller_ai"):
        if run["status"] != "verified":
            raise RunVerificationError("legacy_classification_protocol")
        return _verify_caller_ai_topic_run(
            run, store, evidence_scope="candidate_or_context",
        )
    return _verify_v1_topic_run(run, store)
```

Rename/refactor the existing `_verify_v2_topic_run` into the parameterized caller-AI verifier. Pass the evidence scope to `validate_decision` and `_validate_final_rows`.

Add `evidence_scope` to `_validate_final_rows`; candidate scope checks only `item["text"]`, while legacy scope retains candidate-or-context behavior. For v3, assert `_final_row(...).evidence_source` contains only `source_text`.

Do not modify historical artifact bytes. Export metadata includes caller-AI protocol details for versions 2 and 3.

- [ ] **Step 4: Run focused and complete relevant tests**

Run the Step 2 command, then:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_run_store.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py \
  feedback_hub/tests/test_topic_mining_export.py -q
```

Expected: all tests PASS.

- [ ] **Step 5: Commit verification and compatibility**

```bash
git add feedback_hub/topic_mining/service.py feedback_hub/topic_mining/export.py feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_end_to_end.py feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_api.py
git commit -m "fix: block context-only topic results"
```

### Task 4: Skill v3 Contract and Deployment

**Files:**
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Modify: `codex-skills/mining-feedback-topics/references/review-policy.md`
- Modify: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`
- Verify: `deploy_devcloud.sh`

**Interfaces:**
- Consumes: exact backend capability protocol v3.
- Produces: Skill decisions whose matched evidence comes only from `item.text`.
- Produces: deployed backend and client with matching protocol ownership.

- [ ] **Step 1: Write failing Skill contract tests**

Update `_classification_protocol()` to v3 and assert all Skill surfaces:

```python
assert "exact_candidate_substring" in contract
assert "context is interpretive only" in policy.lower()
assert "solely because" in policy.lower()
assert "item.text" in policy
assert "context_items" in policy
assert "exact caller-ai v3 ownership" in skill.lower()
assert "evidence_not_candidate_grounded" in contract
assert "exact_source_or_context_substring" not in combined_text
```

Add a client test that rejects a v2 capability response before creating a Run.

- [ ] **Step 2: Run Skill tests and verify RED**

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py \
  -k "classification_protocol or candidate_evidence or context" -q
```

Expected: FAIL because the Skill and client still require v2 context evidence.

- [ ] **Step 3: Update Skill, references, and client**

Change client expected capability to:

```python
expected = {
    "version": 3,
    "owner": "caller_ai",
    "candidate_page_default": 20,
    "candidate_page_maximum": 20,
    "matched_evidence": "exact_candidate_substring",
    "partial_acceptance": True,
}
```

Update the Skill under its 500-word limit. In classification instructions, state that context is interpretive only; a matched decision must quote `item.text`; a candidate cannot match solely because context is relevant; vague candidates without self-grounded evidence are `not_matched`.

Update backend contract commands and stable error guidance. Update review policy with the same rule and prohibit substituting a context item under the candidate ID.

- [ ] **Step 4: Run complete verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py \
  feedback_hub/tests/test_topic_mining_caller_classification.py \
  feedback_hub/tests/test_topic_mining_run_store.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py \
  feedback_hub/tests/test_topic_mining_export.py -q --tb=short
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  codex-skills/mining-feedback-topics
PYTHONPYCACHEPREFIX=/tmp/feedback-hub-pycache python3 -m compileall -q \
  feedback_hub/topic_mining codex-skills/mining-feedback-topics/scripts
git diff --check
```

Expected: all tests PASS, Skill validation succeeds, compilation succeeds, and diff check is clean.

- [ ] **Step 5: Commit and push**

```bash
git add codex-skills/mining-feedback-topics feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "docs: require item-local topic decisions"
git push origin feature/mining-feedback-topics-skill
```

- [ ] **Step 6: Deploy and verify production**

```bash
APP_PORT=8000 ./deploy_devcloud.sh
python3 codex-skills/mining-feedback-topics/scripts/topic_backend_client.py capabilities
```

Require production capabilities to return v3 and `exact_candidate_substring`.

Locate the most recent affected v2 Run by scanning its verified rows for `evidence_source=context`. Recreate that stored spec through the public create-run endpoint, which must produce a distinct v3 Run. Page until finding a candidate with a non-empty `context_items`; choose an exact context fragment absent from `item.text`, submit it as a deliberate matched decision, and require `evidence_not_candidate_grounded`. Repair that item to `not_matched`, classify every remaining candidate according to `item.text`, Verify, export, download, and confirm no final row reports `evidence_source=context`. If no production v2 row or v3 candidate has context, record that precondition and use the passing synthetic end-to-end regression as the item-local evidence smoke while still requiring the live capabilities check.
