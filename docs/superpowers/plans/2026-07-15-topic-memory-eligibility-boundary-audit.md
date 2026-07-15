# Topic Memory Eligibility and Boundary Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a low-information memory gate, tighten cross-day topic boundaries, audit risky matches, and run a targeted old-versus-new experiment without modifying the completed baseline artifacts.

**Architecture:** Extend the existing lifecycle decision contract with `low_information`, then keep sample selection and post-match audit in a focused `refinement.py` module. A new resumable experiment runner reads the existing 2026-07-12 and 2026-07-13 artifacts, reruns only high-risk topics, overlays revised decisions onto the complete 413-topic baseline, and emits a separate topic store, low-information pool, audit, workbook, and report.

**Tech Stack:** Python 3.9+, pytest, JSONL, NumPy artifacts already produced by the baseline, Knot agent API with dual routes, `@oai/artifact-tool` for the review workbook.

## Global Constraints

- Do not modify any artifact under `daily_20260712`, `daily_20260713`, or `lifecycle_20260712_20260713`.
- `low_information` retains evidence but never allocates a stable `topic_id`.
- Similarity remains candidate recall only; every lifecycle verdict is an explicit model decision.
- Bugs default to platform/mechanism-specific boundaries; cross-platform feature requests may merge when one product decision handles them.
- Shared generic operations such as sorting, sizing, switches, entry points, visibility, synchronization, or customization cannot establish a relationship across different objects.
- Every targeted topic receives exactly one revised decision, and every one of the 413 daily topics appears exactly once in the overlaid final decision set.
- Model execution uses two Knot routes, four concurrent calls per route, 300-second read timeout, checkpoint/resume, and strict exact-coverage parsing.
- Credentials must exist only in process environment variables.

---

### Task 1: Targeted Review Set Construction

**Files:**
- Create: `feedback_hub/topic_discovery/refinement.py`
- Create: `feedback_hub/tests/test_topic_refinement.py`

**Interfaces:**
- Consumes: full daily topics, baseline decisions, and lifecycle candidate rows.
- Produces: `detect_low_information_review_reasons(topic: dict) -> list[str]` and `select_targeted_topics(daily_topics: list[dict], baseline_decisions: list[dict], candidate_rows: list[dict], same_confidence_threshold: float = 0.8, same_similarity_threshold: float = 0.68, new_similarity_threshold: float = 0.78) -> list[dict]`.

- [ ] **Step 1: Write failing tests for deterministic low-information review candidates**

```python
def test_detect_low_information_review_reasons_marks_ambiguous_topic_only() -> None:
    ambiguous = {"title": "用户提及软件数据备份，意图不明确", "description": "文本过于简短", "members": []}
    concrete = {"title": "Win语音输入后文字不上屏", "description": "结束录音后没有文字结果", "members": []}

    assert detect_low_information_review_reasons(ambiguous) == ["explicit_ambiguity_marker"]
    assert detect_low_information_review_reasons(concrete) == []
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement.py -k low_information -q`

Expected: FAIL because `feedback_hub.topic_discovery.refinement` does not exist.

- [ ] **Step 3: Implement high-precision review-candidate detection**

```python
_AMBIGUITY_MARKERS = (
    "意图不明确", "文本过于简短", "未说明具体", "未指明具体",
    "无法判断", "无上下文", "可能为请求", "可能涉及",
)

def detect_low_information_review_reasons(topic):
    text = "\n".join([str(topic.get("title") or ""), str(topic.get("description") or "")])
    return ["explicit_ambiguity_marker"] if any(marker in text for marker in _AMBIGUITY_MARKERS) else []
```

- [ ] **Step 4: Write failing tests for deduplicated risk-based sample selection**

```python
def test_select_targeted_topics_unions_all_risk_sources_without_duplicates() -> None:
    selected = select_targeted_topics(topics, decisions, candidates)
    assert [row["daily_topic_id"] for row in selected] == ["d1", "d2", "d3", "d4", "d5"]
    assert selected[0]["selection_reasons"] == ["baseline_uncertain"]
    assert "many_to_one_same_topic" in selected[2]["selection_reasons"]
    assert selected[-1]["selection_reasons"] == ["low_information_candidate"]
```

The fixture must cover baseline uncertain, baseline possible-subtopic, low-confidence same, low-similarity same, high-similarity new, many-to-one same, and a deterministic low-information candidate.

- [ ] **Step 5: Run the selection test and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement.py -k select_targeted -q`

Expected: FAIL because `select_targeted_topics` is missing.

- [ ] **Step 6: Implement deterministic sample selection**

Build maps by `daily_topic_id`, derive the selected historical similarity or top-candidate similarity, collect reason sets, add every member of a many-to-one `same_topic` group, and return rows sorted by ID. Each row contains `daily_topic`, `candidates`, `baseline_decision`, and sorted `selection_reasons`. Do not place the baseline verdict inside the model-facing `daily_topic` or `candidates` objects.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add feedback_hub/topic_discovery/refinement.py feedback_hub/tests/test_topic_refinement.py
git commit -m "feat: select risky topic boundary samples"
```

### Task 2: Five-Verdict Lifecycle Prompt and Parser

**Files:**
- Modify: `feedback_hub/topic_discovery/lifecycle_matching.py:20,104-159,162-207`
- Modify: `feedback_hub/tests/test_topic_lifecycle_matching.py`

**Interfaces:**
- Consumes: lifecycle batches containing complete daily-topic facts and zero or more historical candidates.
- Produces: revised `build_lifecycle_match_prompt(batch: dict) -> str`, `parse_lifecycle_match_reply(...) -> list[dict]`, and a candidate plan that sends no-candidate topics to the model for eligibility judgment.

- [ ] **Step 1: Write failing prompt tests for the approved boundary rules**

```python
def test_lifecycle_prompt_includes_memory_eligibility_and_boundary_policies() -> None:
    prompt = build_lifecycle_match_prompt({"batch_id": "b1", "items": []})
    assert "low_information" in prompt
    assert "same product object or capability" in prompt
    assert "sorting, sizing, switches, entry points" in prompt
    assert "Bugs are platform- or mechanism-specific by default" in prompt
    assert "Feature requests may match across platforms" in prompt
```

- [ ] **Step 2: Write failing parser tests for `low_information` invariants**

```python
def test_parse_lifecycle_reply_accepts_low_information_without_history() -> None:
    reply = '{"decisions":[{"daily_topic_id":"d1","verdict":"low_information","historical_topic_id":null,"confidence":0.9,"reason":"对象与症状均不明确"}]}'
    decisions = parse_lifecycle_match_reply(reply, allowed_daily_topic_ids={"d1"}, allowed_historical_topic_ids={"t1"})
    assert decisions[0]["verdict"] == "low_information"
    assert decisions[0]["historical_topic_id"] is None

def test_parse_lifecycle_reply_rejects_low_information_with_history() -> None:
    with pytest.raises(ValueError, match="low_information"):
        parse_lifecycle_match_reply(reply_with_history, allowed_daily_topic_ids={"d1"}, allowed_historical_topic_ids={"t1"})
```

- [ ] **Step 3: Write a failing candidate-plan test for no-candidate eligibility calls**

Update the existing test so both `d1` and `d2` occur in model batches, `model_decision_topics == 2`, and `deterministic_new_topics == 0`.

- [ ] **Step 4: Run Task 2 tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_lifecycle_matching.py -q`

Expected: failures for missing `low_information` semantics and the old no-candidate batching behavior.

- [ ] **Step 5: Implement the five-verdict contract**

```python
_VERDICTS = {"low_information", "same_topic", "new_topic", "possible_subtopic", "uncertain"}

if verdict in {"new_topic", "low_information"}:
    if verdict == "low_information" and historical_topic_id is not None:
        raise ValueError("low_information cannot reference historical_topic_id")
    historical_topic_id = None
elif historical_topic_id not in allowed_historical_topic_ids:
    raise ValueError(f"unknown historical_topic_id: {historical_topic_id}")
```

Batch every candidate row, including rows with no historical candidates. The prompt must say that no candidates implies either `new_topic` or `low_information`, depending on evidence sufficiency.

- [ ] **Step 6: Run Task 2 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_lifecycle_matching.py -q`

Expected: all lifecycle matching tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add feedback_hub/topic_discovery/lifecycle_matching.py feedback_hub/tests/test_topic_lifecycle_matching.py
git commit -m "feat: add topic memory eligibility verdict"
```

### Task 3: Low-Information Lifecycle Events and Pool

**Files:**
- Modify: `feedback_hub/topic_discovery/lifecycle.py:9-14,40-151`
- Modify: `feedback_hub/tests/test_topic_lifecycle.py`
- Modify: `feedback_hub/topic_discovery/refinement.py`
- Modify: `feedback_hub/tests/test_topic_refinement.py`

**Interfaces:**
- Produces: `low_information_held` events, an unchanged stable store for held topics, and `build_low_information_pool(daily_topics: list[dict], decisions: list[dict]) -> list[dict]`.

- [ ] **Step 1: Write failing lifecycle-event tests**

```python
def test_low_information_event_does_not_allocate_or_persist_topic() -> None:
    events = match_daily_topics([_daily("d1", "无法使用")], historical, decisions=[{
        "daily_topic_id": "d1", "verdict": "low_information", "historical_topic_id": None,
        "reason": "对象与症状不明确",
    }])
    assert events[0]["event_type"] == "low_information_held"
    assert events[0]["topic_id"] is None
    assert apply_topic_events(historical, [_daily("d1", "无法使用")], events) == historical
```

- [ ] **Step 2: Run the event test and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_lifecycle.py -k low_information -q`

Expected: FAIL because `low_information` is unsupported.

- [ ] **Step 3: Implement non-persisting lifecycle behavior**

Add `"low_information": "low_information_held"` to `_VERDICT_TO_EVENT`. In `match_daily_topics`, emit `topic_id=None` without incrementing the topic counter. In `apply_topic_events`, recognize `low_information_held` and continue without changing the topic map.

- [ ] **Step 4: Write failing low-information-pool tests**

```python
def test_build_low_information_pool_preserves_evidence_and_media() -> None:
    pool = build_low_information_pool([topic_with_media], [low_information_decision])
    assert pool[0]["daily_topic_id"] == "d1"
    assert pool[0]["conversation_ids"] == ["c1"]
    assert pool[0]["evidence_links"] == ["https://example.test/chat"]
    assert pool[0]["has_media_evidence"] is True
    assert pool[0]["media_appendix_eligible"] is True
```

- [ ] **Step 5: Implement the pool builder**

Select only `low_information` decisions, join their complete daily topic, derive `has_media_evidence` from members, preserve the model reason/confidence, and sort by `daily_topic_id`.

- [ ] **Step 6: Run Task 3 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_lifecycle.py feedback_hub/tests/test_topic_refinement.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add feedback_hub/topic_discovery/lifecycle.py feedback_hub/topic_discovery/refinement.py feedback_hub/tests/test_topic_lifecycle.py feedback_hub/tests/test_topic_refinement.py
git commit -m "feat: hold low-information topics outside memory"
```

### Task 4: Decision Overlay and Post-Match Audit

**Files:**
- Modify: `feedback_hub/topic_discovery/refinement.py`
- Modify: `feedback_hub/tests/test_topic_refinement.py`

**Interfaces:**
- Produces: `overlay_revised_decisions(all_daily_topic_ids: list[str], baseline_decisions: list[dict], revised_decisions: list[dict]) -> list[dict]` and `audit_refined_decisions(daily_topics: list[dict], historical_topics: list[dict], decisions: list[dict], candidate_rows: list[dict], low_confidence_threshold: float = 0.8, high_similarity_threshold: float = 0.78) -> list[dict]`.

- [ ] **Step 1: Write failing exact-overlay tests**

```python
def test_overlay_revised_decisions_replaces_only_targeted_rows() -> None:
    result = overlay_revised_decisions(["d1", "d2"], baseline, revised)
    assert [row["verdict"] for row in result] == ["low_information", "same_topic"]
    assert result[0]["decision_source"] == "targeted_refinement"
    assert result[1]["decision_source"] == "baseline_reused"
```

Also reject duplicate revised IDs, unknown IDs, missing baseline coverage, and duplicate baseline IDs.

- [ ] **Step 2: Run overlay tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement.py -k overlay -q`

Expected: FAIL because the overlay function is missing.

- [ ] **Step 3: Implement exact decision overlay**

Validate the complete ID set before returning sorted rows. Copy decisions rather than mutating baseline objects and add `decision_source` to every output row.

- [ ] **Step 4: Write failing audit tests for every flag**

Use focused fixtures that produce:

```python
assert audit_by_id["d1"]["audit_flags"] == ["many_to_one_same_topic"]
assert "generic_operation_boundary_risk" in audit_by_id["d2"]["audit_flags"]
assert "platform_boundary_risk" in audit_by_id["d3"]["audit_flags"]
assert "low_confidence_same_topic" in audit_by_id["d4"]["audit_flags"]
assert "high_similarity_new_topic" in audit_by_id["d5"]["audit_flags"]
assert audit_by_id["d6"]["audit_flags"] == ["low_information_media"]
```

Generic-operation risk requires a shared configured operation token and disjoint non-unknown feature candidates. Platform risk requires a bug/mixed relationship with disjoint non-empty platform sets.

- [ ] **Step 5: Run audit tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement.py -k audit -q`

Expected: FAIL because the audit function is missing.

- [ ] **Step 6: Implement deterministic audit rows**

Each row contains `daily_topic_id`, verdict, selected or top historical topic, selected similarity, sorted `audit_flags`, current title, historical title, evidence link, and media state. Audit functions flag but never rewrite decisions.

- [ ] **Step 7: Run Task 4 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement.py -q`

Expected: all refinement tests pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add feedback_hub/topic_discovery/refinement.py feedback_hub/tests/test_topic_refinement.py
git commit -m "feat: audit refined topic boundaries"
```

### Task 5: Resumable Targeted Experiment Runner

**Files:**
- Create: `scripts/run_topic_boundary_refinement.py`
- Create: `feedback_hub/tests/test_topic_refinement_runner.py`

**Interfaces:**
- Consumes: baseline lifecycle directory, current/historical daily directories, two Knot routes, and an isolated output directory.
- Produces all JSONL, summary, store, pool, audit, and manifest artifacts named in the design.

- [ ] **Step 1: Write failing runner-helper tests**

Test `build_refinement_batches(targeted_rows, historical_daily_topics, max_batch_size=10)`, `summarize_refinement(...)`, and artifact path construction. Verify that model batches exclude `baseline_decision`, include full current/historical facts, and cover each target exactly once.

- [ ] **Step 2: Run runner tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement_runner.py -q`

Expected: import failure because the runner does not exist.

- [ ] **Step 3: Implement artifact loading and enriched batches**

Map `topic:NNNNNN` to the historical daily topics sorted by `daily_topic_id`. Enrich current and candidate records with platform, feedback-type, feature-candidate, representative summary, evidence, and media fields. Write `targeted_topics.jsonl` and `revised_lifecycle_batches.jsonl` before network calls.

- [ ] **Step 4: Implement dual-route execution and strict completion gate**

Use `run_lifecycle_batches_multi_channel` with `concurrency_per_route=4`, `request_timeout=300`, `max_retries=1`, and `resume=True` when requested. Raise before final artifacts when call failures, parse failures, or target coverage mismatch remain.

- [ ] **Step 5: Implement full overlay and outputs**

Flatten revised decisions, overlay them onto all 413 baseline decisions, generate lifecycle events, apply the store, build the low-information pool, run the audit, and write:

```text
revised_decisions.jsonl
full_overlaid_decisions.jsonl
low_information_pool.jsonl
boundary_audit.jsonl
topic_events.jsonl
topic_store.jsonl
comparison_summary.json
manifest.json
```

The manifest contains input/output hashes, route names and URLs, code hashes, settings, and counts, but no environment variable values.

- [ ] **Step 6: Run runner tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_refinement_runner.py -q`

Expected: all tests pass without network access.

- [ ] **Step 7: Commit Task 5**

```bash
git add scripts/run_topic_boundary_refinement.py feedback_hub/tests/test_topic_refinement_runner.py
git commit -m "feat: run targeted topic boundary refinement"
```

### Task 6: Execute the Targeted Experiment

**Files:**
- Create under: `feedback_hub/data/topic_discovery_prototype/refinement_20260712_20260713/`

**Interfaces:**
- Uses the Task 5 runner and existing baseline artifacts.
- Produces the complete isolated refinement artifact set.

- [ ] **Step 1: Run sample construction without model calls**

Run the runner in `--prepare-only` mode. Record the total target count and reason distribution. Verify all 22 baseline uncertain and all 36 baseline possible-subtopic topics are included.

- [ ] **Step 2: Inspect model request volume**

Verify target IDs are unique, target rows equal the union of selection reasons, and batch count equals `ceil(target_count / 10)`.

- [ ] **Step 3: Run the two Knot routes**

Provide `KNOT_API_TOKEN_A` and `KNOT_API_TOKEN_B` only through process environment variables. Run at four concurrent requests per route with checkpoint/resume. If a run ends with retryable failures, rerun the identical command with `--resume`.

- [ ] **Step 4: Validate exact output coverage**

Verify revised decisions equal the target count, full overlaid decisions equal 413, lifecycle events equal 413, no `low_information` decision has a topic ID, and stable store count equals `381 + new_topic + possible_subtopic + uncertain` after accounting for same-topic extensions.

### Task 7: Review Workbook and Result Report

**Files:**
- Create: `feedback_hub/data/topic_discovery_prototype/build_refinement_review.mjs`
- Create under: `outputs/topic_boundary_refinement_20260712_20260713/`

**Interfaces:**
- Consumes the Task 6 artifacts and baseline topic/decision facts.
- Produces `topic_boundary_refinement_review.xlsx` and `topic_boundary_refinement_report.md`.

- [ ] **Step 1: Build the comparison workbook with `@oai/artifact-tool`**

Create sheets:

- `结果概览`: target count, old/new verdict distribution, changed decisions, low-information count, pool media count, and audit-flag counts;
- `新旧对照`: old/new verdict and historical topic, reason, confidence, audit flags, evidence link, and blank human columns;
- `低信息池`: held topic, evidence, reason, media state, and appendix eligibility;
- `边界审计`: one row per audited topic;
- `口径说明`: approved rules and standard values.

Add filters, frozen headers, wrapped text, restrained status colors, plain-text source URLs, and data validation for human review.

- [ ] **Step 2: Write the Markdown report**

Report exact changes, representative fixes, remaining risks, low-information examples, generic-operation and platform audit findings, and whether the revised policy is ready for a seven-day run.

- [ ] **Step 3: Verify workbook values and formulas**

Inspect key ranges, scan for formula errors, reconcile workbook row counts to JSONL, export, reopen the `.xlsx`, and inspect the reopened comparison sheet.

- [ ] **Step 4: Render every workbook sheet**

Render all five sheets, visually inspect clipping and readability, and patch the builder until the workbook is legible.

### Task 8: Final Verification

**Files:**
- Modify only files from Tasks 1-7 when verification finds a defect.

- [ ] **Step 1: Run the complete focused test suite**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_refinement.py \
  feedback_hub/tests/test_topic_refinement_runner.py \
  feedback_hub/tests/test_topic_lifecycle.py \
  feedback_hub/tests/test_topic_lifecycle_matching.py \
  feedback_hub/tests/test_topic_discovery.py \
  feedback_hub/tests/test_topic_discovery_runner.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run artifact integrity checks**

Recalculate manifest hashes, verify exact target/full coverage, confirm stable-store and pool counts, run `unzip -t` on the workbook, and scan source plus artifacts for the two token values.

- [ ] **Step 3: Review repository scope**

Use `git diff --check`, inspect only files created or modified by this plan, and leave unrelated dirty-worktree files untouched.

- [ ] **Step 4: Commit final code adjustments**

```bash
git add feedback_hub/topic_discovery feedback_hub/tests scripts/run_topic_boundary_refinement.py feedback_hub/data/topic_discovery_prototype/build_refinement_review.mjs
git commit -m "feat: refine dynamic topic memory boundaries"
```
