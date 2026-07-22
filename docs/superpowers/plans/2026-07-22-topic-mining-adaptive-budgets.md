# Topic Mining Adaptive Budgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scale standard classification by elapsed days, bound sampled review work, and export every verified match without weakening coverage disclosures.

**Architecture:** A focused budget module computes immutable per-Run candidate and review budgets from the validated scope. The service consumes persisted budgets when selecting candidates and building a deterministic mandatory-plus-sampled review queue. Export and API scope logic report candidate truncation independently from export row count.

**Tech Stack:** Python 3, FastAPI, SQLite, openpyxl, pytest, Codex Skill Markdown

## Global Constraints

- Standard candidate budget is `min(500, max(100, effective_days * 80))` where `effective_days=max(1, ceil(elapsed milliseconds / 24 hours))`.
- Standard review QA allowance is `min(80, effective_days * 20)`; mandatory review never consumes or obeys this allowance.
- Exhaustive candidate safety maximum remains 5,000.
- Every verified matched row is exported in both standard and exhaustive modes.
- `result_scope=representative` exactly when retrieved candidates exceed classified candidates; otherwise it is `reviewed`.
- Existing selected-candidate and review-queue artifacts remain frozen on resume.
- New tuning controls remain backend-owned and never enter the topic spec or Skill arguments.
- Source data and formal labels remain read-only.

---

## File Structure

- Create `feedback_hub/topic_mining/budgets.py`: elapsed-day calculation and serializable immutable candidate/review budgets.
- Modify `feedback_hub/topic_mining/config.py`: backend-owned minimum, per-day, and maximum values.
- Modify `feedback_hub/topic_mining/api.py`: persist new-Run budgets, advertise policy, and report uncapped result scope.
- Modify `feedback_hub/topic_mining/service.py`: consume persisted candidate budget, persist effective funnel fields, and use bounded review planning.
- Modify `feedback_hub/topic_mining/review.py`: deterministic mandatory-plus-stratified QA queue planning.
- Modify `feedback_hub/topic_mining/export.py`: export all verified matches.
- Create `feedback_hub/tests/test_topic_mining_budgets.py`: duration-boundary unit tests.
- Modify `feedback_hub/tests/test_topic_mining_service.py`, `test_topic_mining_review.py`, `test_topic_mining_export.py`, `test_topic_mining_api.py`, and `test_topic_mining_end_to_end.py`: integration and compatibility coverage.
- Modify `codex-skills/mining-feedback-topics/SKILL.md`, `references/backend-contract.md`, and `references/topic-spec.md`: caller-facing policy and disclosure rules.
- Modify `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`: distributable Skill contract tests.

---

### Task 1: Persist and consume dynamic candidate budgets

**Files:**
- Create: `feedback_hub/topic_mining/budgets.py`
- Create: `feedback_hub/tests/test_topic_mining_budgets.py`
- Modify: `feedback_hub/topic_mining/config.py`
- Modify: `feedback_hub/topic_mining/api.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/tests/test_topic_mining_api.py`
- Modify: `feedback_hub/tests/test_topic_mining_service.py`

**Interfaces:**
- Produces: `effective_days(spec: TopicSpec) -> int`.
- Produces: `candidate_budget(spec: TopicSpec, config: TopicMiningConfig) -> dict[str, int | str]`.
- Produces: `review_sample_budget(spec: TopicSpec, config: TopicMiningConfig) -> dict[str, int]` for Task 2.
- Persists: `manifest.candidate_budget` and `manifest.review_budget` when a Run snapshot is created.
- Consumes: persisted `manifest.candidate_budget.effective_limit` during hybrid recall selection.

- [x] **Step 1: Write failing duration and mode budget tests**

Create parameterized tests with exact boundaries:

```python
@pytest.mark.parametrize(("elapsed_ms", "days", "limit"), [
    (60 * 60 * 1000, 1, 100),
    (24 * 60 * 60 * 1000, 1, 100),
    (24 * 60 * 60 * 1000 + 1, 2, 160),
    (3 * 24 * 60 * 60 * 1000, 3, 240),
    (6 * 24 * 60 * 60 * 1000, 6, 480),
    (7 * 24 * 60 * 60 * 1000, 7, 500),
    (180 * 24 * 60 * 60 * 1000, 180, 500),
])
def test_standard_candidate_budget_scales_by_elapsed_days(elapsed_ms, days, limit):
    spec = spec_with_elapsed_ms(elapsed_ms)
    budget = candidate_budget(spec, TopicMiningConfig())
    assert budget == {
        "mode": "standard", "effective_days": days,
        "minimum": 100, "per_day": 80, "maximum": 500,
        "effective_limit": limit,
    }

def test_exhaustive_budget_keeps_5000_safety_limit():
    budget = candidate_budget(spec_with_elapsed_ms(24 * 60 * 60 * 1000, mode="exhaustive"), TopicMiningConfig())
    assert budget["effective_limit"] == 5000
```

- [x] **Step 2: Run the budget tests and confirm RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_budgets.py -q`

Expected: collection fails because `feedback_hub.topic_mining.budgets` does not exist.

- [x] **Step 3: Implement the immutable budget module and configuration**

Add these configuration fields and remove `standard_result_limit`:

```python
standard_candidate_min: int = 100
standard_candidates_per_day: int = 80
standard_candidate_max: int = 500
review_samples_per_day: int = 20
review_sample_max: int = 80
exhaustive_candidate_limit: int = 5000
```

Implement the elapsed calculation from timezone-aware validated instants:

```python
_DAY_MS = 24 * 60 * 60 * 1000

def effective_days(spec: TopicSpec) -> int:
    elapsed_ms = max(1, int(
        (spec.scope.end_time - spec.scope.start_time).total_seconds() * 1000
    ))
    return max(1, (elapsed_ms + _DAY_MS - 1) // _DAY_MS)

def candidate_budget(spec: TopicSpec, config: TopicMiningConfig) -> dict[str, int | str]:
    days = effective_days(spec)
    if spec.mode == "exhaustive":
        return {
            "mode": "exhaustive", "effective_days": days,
            "maximum": config.exhaustive_candidate_limit,
            "effective_limit": config.exhaustive_candidate_limit,
        }
    return {
        "mode": "standard", "effective_days": days,
        "minimum": config.standard_candidate_min,
        "per_day": config.standard_candidates_per_day,
        "maximum": config.standard_candidate_max,
        "effective_limit": min(
            config.standard_candidate_max,
            max(config.standard_candidate_min, days * config.standard_candidates_per_day),
        ),
    }

def review_sample_budget(spec: TopicSpec, config: TopicMiningConfig) -> dict[str, int]:
    days = effective_days(spec)
    return {
        "effective_days": days,
        "per_day": config.review_samples_per_day,
        "maximum": config.review_sample_max,
        "sample_limit": min(config.review_sample_max, days * config.review_samples_per_day),
    }
```

- [x] **Step 4: Add failing API and service integration assertions**

Update capabilities expectations so standard retains the compatible maximum but advertises dynamic policy and no result cap:

```python
assert payload["run_modes"]["standard"] == {
    "candidate_limit": 500,
    "result_limit": None,
    "candidate_budget": {"minimum": 100, "per_day": 80, "maximum": 500},
    "review_sample_budget": {"per_day": 20, "maximum": 80},
}
```

Add one-day and seven-day service cases asserting selected counts 100 and 500. Assert the snapshot manifest contains the exact persisted budget before asynchronous work starts, and assert a changed config cannot alter a Run that already has a valid `selected_candidates.jsonl` artifact.

- [x] **Step 5: Persist budgets at Run creation and consume them in service**

In `create_run`, add both budget dictionaries to the initial manifest. Replace `_classification_candidate_limit(spec, config)` with:

```python
def _classification_candidate_limit(
    spec: TopicSpec,
    config: TopicMiningConfig,
    manifest: Mapping[str, Any],
) -> int:
    persisted = manifest.get("candidate_budget")
    if isinstance(persisted, Mapping):
        value = persisted.get("effective_limit")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return int(candidate_budget(spec, config)["effective_limit"])
```

Pass the manifest at candidate selection. Preserve the selected artifact path and stage-validity behavior unchanged. Record `candidate_budget` in the funnel-facing Run response without accepting it from clients.

- [x] **Step 6: Run focused Task 1 tests and confirm GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_budgets.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_service.py \
  -q
```

Expected: all selected tests pass; one-day selects at most 100 and seven-day selects at most 500.

- [ ] **Step 7: Commit Task 1**

```bash
git add feedback_hub/topic_mining/budgets.py feedback_hub/topic_mining/config.py \
  feedback_hub/topic_mining/api.py feedback_hub/topic_mining/service.py \
  feedback_hub/tests/test_topic_mining_budgets.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_service.py
git commit -m "feat: scale topic candidates by time range"
```

### Task 2: Replace unbounded risk review with deterministic bounded QA

**Files:**
- Modify: `feedback_hub/topic_mining/review.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/tests/test_topic_mining_review.py`
- Modify: `feedback_hub/tests/test_topic_mining_service.py`

**Interfaces:**
- Consumes: persisted `manifest.review_budget.sample_limit` from Task 1.
- Produces: `ReviewQueuePlan(rows, mandatory_count, sample_limit, sampled_count)`.
- Produces: `plan_review_queue(run_id, classifications, recall_by_id, *, contexts, sample_limit) -> ReviewQueuePlan`.
- Persists: completed `manifest.review_budget` counts and `manifest.review_queue.item_count`.

- [x] **Step 1: Write failing review-plan tests**

Cover these exact properties:

```python
def test_review_plan_keeps_all_mandatory_items_beyond_sample_limit():
    values = [matched(f"m-{i}", .70, needs_review=True) for i in range(25)]
    plan = plan_review_queue("run", values, recalls(values), sample_limit=4)
    assert plan.mandatory_count == 25
    assert plan.sampled_count == 0
    assert len(plan.rows) == 25

def test_review_plan_balances_and_refills_four_qa_strata():
    plan = plan_review_queue("run", qa_fixture(), qa_recalls(), sample_limit=8)
    assert plan.sample_limit == 8
    assert plan.sampled_count == 8
    assert len({row["item_id"] for row in plan.rows}) == len(plan.rows)

def test_review_plan_is_stable_for_same_run_and_changes_hash_order_for_other_run():
    first = plan_review_queue("run-a", values, recalls, sample_limit=8)
    again = plan_review_queue("run-a", values, recalls, sample_limit=8)
    other = plan_review_queue("run-b", values, recalls, sample_limit=8)
    assert first == again
    assert [row["item_id"] for row in first.rows] != [row["item_id"] for row in other.rows]
```

Also assert an item in vector-only and negative-conflict strata appears once, unused quota is redistributed, and context/source fields remain unchanged.

- [x] **Step 2: Run review tests and confirm RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_review.py -q`

Expected: import or assertion failures because `ReviewQueuePlan` and `plan_review_queue` do not exist.

- [x] **Step 3: Implement deterministic stratified planning**

Add:

```python
@dataclass(frozen=True)
class ReviewQueuePlan:
    rows: tuple[dict[str, Any], ...]
    mandatory_count: int
    sample_limit: int
    sampled_count: int
```

Build the mandatory ID set first. Build the four QA strata only from remaining IDs. Order each stratum with:

```python
def _sample_key(run_id: str, item_id: str, stratum: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{run_id}:{item_id}:{stratum}".encode("utf-8")).hexdigest()
    return digest, item_id
```

Allocate one item per non-empty stratum in round-robin order until `sample_limit` is exhausted or no new ID remains. This naturally redistributes unused capacity and deduplicates overlaps. A sampled row retains every applicable `review_reason`, not only the stratum that selected it.

- [x] **Step 4: Wire service persistence and compatibility**

When building a new review queue, call `plan_review_queue` with the persisted sample limit, persist `plan.rows`, and update:

```python
manifest["review_budget"].update({
    "mandatory_count": plan.mandatory_count,
    "sampled_count": plan.sampled_count,
    "queue_count": len(plan.rows),
})
```

If the existing review-queue stage is valid, do not invoke the planner and do not replace its artifact or obligations. For legacy manifests without `review_budget`, compute the policy once from the persisted spec and current config only when no valid queue exists.

- [x] **Step 5: Run focused Task 2 tests and confirm GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_review.py \
  feedback_hub/tests/test_topic_mining_service.py \
  -q
```

Expected: all pass; mandatory counts may exceed 80, sampled counts never exceed the persisted limit, and same-Run queues are byte-stable.

- [ ] **Step 6: Commit Task 2**

```bash
git add feedback_hub/topic_mining/review.py feedback_hub/topic_mining/service.py \
  feedback_hub/tests/test_topic_mining_review.py \
  feedback_hub/tests/test_topic_mining_service.py
git commit -m "feat: bound topic review sampling"
```

### Task 3: Remove confirmed-result export truncation

**Files:**
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/topic_mining/export.py`
- Modify: `feedback_hub/topic_mining/api.py`
- Modify: `feedback_hub/tests/test_topic_mining_export.py`
- Modify: `feedback_hub/tests/test_topic_mining_api.py`
- Modify: `feedback_hub/tests/test_topic_mining_end_to_end.py`

**Interfaces:**
- Consumes: verified `final_reviewed.jsonl`, `retrieved_candidate_count`, and `classified_count`.
- Produces: all final matched rows in `final_results.jsonl` and `feedback_list.xlsx`.
- Produces: identical result-scope metadata in Run API, quality report, JSONL export manifest, and workbook metadata.

- [x] **Step 1: Replace capped-export tests with failing all-row expectations**

Change the 130-row standard fixture to expect all 130 rows and add a 321-row workbook fixture:

```python
def test_standard_export_keeps_all_321_confirmed_matches(tmp_path):
    run, store, artifact_dir = verified_run_with_rows(tmp_path, count=321)
    manifest = persisted_manifest(artifact_dir)
    manifest.update({"retrieved_candidate_count": 570, "classified_count": 500})
    persist_manifest(store, run, artifact_dir, manifest)

    jsonl_path = export_topic_run(run["run_id"], "jsonl", store=store)
    xlsx_path = export_topic_run(run["run_id"], "xlsx", store=store)

    assert len(_read_jsonl(jsonl_path)) == 321
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    assert workbook["反馈清单"].max_row == 322
    assert dict(workbook["导出元数据"].iter_rows(values_only=True)) == {
        "mode": "standard", "result_scope": "representative",
        "matched_total": 321, "returned_feedback": 321,
        "possibly_more_matches": True,
    }
    workbook.close()
```

Add the complementary exact-coverage case with `retrieved_candidate_count == classified_count` expecting `reviewed` and `possibly_more_matches=false`.

- [x] **Step 2: Run export/API tests and confirm RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_api.py \
  -q
```

Expected: standard export still contains 100 rows and old scope values disagree.

- [x] **Step 3: Simplify shared result-scope calculation**

Remove `_STANDARD_RESULT_LIMIT` and stop calling `select_representative_results` from service/export. Implement:

```python
possibly_more_matches = retrieved > manifest_classified
return {
    "mode": spec.mode,
    "result_scope": "representative" if possibly_more_matches else "reviewed",
    "matched_total": matched_total,
    "returned_feedback": matched_total,
    "possibly_more_matches": possibly_more_matches,
}
```

Apply the same fallback validation for malformed legacy count fields that `_result_scope` currently performs. Update `_public_result_scope` in the API to use identical semantics for verified and pre-export Runs.

- [x] **Step 4: Export every verified row atomically**

Replace standard/exhaustive branching with:

```python
selected_rows = list(rows)
```

Keep sorting, required-field validation, formula escaping, manifest CAS publication, and artifact allowlisting unchanged. Ensure a failed workbook build does not publish a partial terminal mutation.

- [x] **Step 5: Run Task 3 tests and end-to-end coverage**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py \
  -q
```

Expected: all pass; standard 321-row fixtures produce 321 JSONL rows and 322 workbook rows including the header.

- [ ] **Step 6: Commit Task 3**

```bash
git add feedback_hub/topic_mining/service.py feedback_hub/topic_mining/export.py \
  feedback_hub/topic_mining/api.py feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py
git commit -m "feat: export every confirmed topic match"
```

### Task 4: Update the distributable Skill and verify the complete workflow

**Files:**
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Modify: `codex-skills/mining-feedback-topics/references/topic-spec.md`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`
- Modify: `WORKSPACE_GUIDE.md` only if deployment or automation topology changes; this feature does not currently require such a change.

**Interfaces:**
- Consumes: capabilities dynamic budget fields and uncapped result metadata.
- Produces: Skill guidance that treats backend budgets as read-only policy and delivers every returned confirmed row.

- [x] **Step 1: Write failing Skill contract assertions**

Require the Skill and backend reference to say:

```python
assert "100" in backend
assert "80 per effective day" in backend
assert "500" in backend
assert "all verified matched rows" in backend
assert "result_limit=null" in backend
assert "candidate tuning remains backend-owned" in body
assert "standard classification is always 500" not in body
assert "export at 100" not in body
```

Update the capabilities fixture expectation in package tests to include the dynamic policy object exactly.

- [x] **Step 2: Run package tests and confirm RED**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: failures on old fixed-500/fixed-100 wording.

- [x] **Step 3: Update concise Skill workflow and references**

Document that standard scales from 100 to 500 based on backend-computed effective days; seven days and longer reach the 500 maximum. State that the Agent reviews every returned queue page but the backend bounds sampled QA and always retains mandatory review. State that export returns all confirmed matches and that `representative/possibly_more_matches=true` still discloses unclassified retrieved candidates.

Do not expose `minimum`, `per_day`, `maximum`, or review sampling as topic-spec fields or CLI flags. Keep `SKILL.md` under its existing 500-word test limit by placing numeric detail in `references/backend-contract.md`.

- [x] **Step 4: Run full topic-mining regression and Skill validation**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining*.py \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  codex-skills/mining-feedback-topics
```

Expected: all tests pass and validator prints `Skill is valid!`.

- [ ] **Step 5: Commit Task 4**

```bash
git add codex-skills/mining-feedback-topics/SKILL.md \
  codex-skills/mining-feedback-topics/references/backend-contract.md \
  codex-skills/mining-feedback-topics/references/topic-spec.md \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "docs: teach skill adaptive topic budgets"
```

- [ ] **Step 6: Deploy and perform non-destructive production verification**

Run:

```bash
git push origin feature/mining-feedback-topics-skill
APP_PORT=8000 ./deploy_devcloud.sh
curl -fsS http://charvelxia-any2.devcloud.woa.com:8000/api/topic-mining/capabilities
```

Verify capabilities reports standard maximum 500, minimum 100, 80 per effective day, review sampling 20 per day capped at 80, and `result_limit=null`. Run deterministic fixture tests against deployed code or a copied Run artifact. Do not create or mutate a production Run solely for smoke testing.

- [ ] **Step 7: Final audit**

Run:

```bash
git diff --check
git status --short
git log -5 --oneline
```

Expected: only the user-owned untracked `WORKSPACE_GUIDE.md` may remain; all implementation commits are on `feature/mining-feedback-topics-skill` and pushed to the existing PR.
