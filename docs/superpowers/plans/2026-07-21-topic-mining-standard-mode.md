# Topic Mining Standard Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary Skill runs default to the last two weeks, classify at most 500 diverse candidates, and export at most 100 representative feedback rows while preserving an explicit exhaustive mode.

**Architecture:** Keep strict canonical topic specs with explicit timestamps, but let the bundled validator fill a missing time pair from the backend-advertised 14-day policy before submission. The topic backend separates recall pool, classification budget, and export budget; RRF orders candidates, deterministic strata preserve time/channel/query diversity, and the classifier still decides membership. Standard output reports representative scope instead of claiming completeness.

**Tech Stack:** Existing topic-mining Python backend, JSON Schema, OpenAI-compatible classifier, FastAPI, standard-library Skill client, pytest.

## Global Constraints

- Missing query time defaults to the most recent 14 days; an explicitly supplied time range is never replaced.
- Supplying only one time boundary remains a material ambiguity and must trigger one direct question.
- `standard` is the default mode; `exhaustive` is used only when the user explicitly requests all/complete results.
- Standard mode sends at most 500 deduplicated candidates to backend semantic classification and exports at most 100 confirmed rows.
- Backend classifier batch size remains at most 20; Skill review queue pages contain at most 50 rows.
- BM25/vector/RRF scores are recall evidence and never directly produce a matched label.
- When more matches may exist, output must say `result_scope=representative` and `possibly_more_matches=true`.
- No API token is required in the internal/VPN deployment; optional token compatibility may remain for future deployments.
- Existing run snapshots, evidence validation, link validation, and formal-label read-only guarantees remain mandatory.

---

## File Structure

- Modify `feedback_hub/topic_mining/contracts.py`: optional `mode`, canonical default, and serialization.
- Modify `feedback_hub/topic_mining/config.py`: recall pool, standard/exhaustive classification budgets, export budget, review page limit.
- Create `feedback_hub/topic_mining/diversity.py`: deterministic candidate/result strata and round-robin selection.
- Modify `feedback_hub/topic_mining/retrieval.py`: preserve a recall pool larger than the classification budget and audit both counts.
- Modify `feedback_hub/topic_mining/service.py`: select classification candidates by run mode and persist result-scope metadata.
- Modify `feedback_hub/topic_mining/export.py`: limit only exported standard-mode matches while keeping full classification artifacts.
- Modify `feedback_hub/topic_mining/api.py`: advertise policies and paginate review queue.
- Modify Skill validator, schema, client, `SKILL.md`, and references for default time, mode, paging, and no-token internal setup.
- Modify existing topic-mining and Skill package tests; add `feedback_hub/tests/test_topic_mining_diversity.py`.

### Task 1: Canonical Run Mode and Two-Week Spec Preparation

**Files:**
- Modify: `feedback_hub/topic_mining/contracts.py`
- Modify: `feedback_hub/tests/test_topic_mining_contracts.py`
- Modify: `codex-skills/mining-feedback-topics/scripts/validate_topic_spec.py`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Produces: `TopicSpec.mode: Literal["standard", "exhaustive"]` with default `standard`.
- Produces Skill validator arguments: `--default-now RFC3339 --default-days 14`.
- Keeps backend `validate_topic_spec()` strict about receiving explicit `scope.start_time` and `scope.end_time`.

- [ ] **Step 1: Write failing contract and validator tests**

```python
def test_topic_spec_defaults_mode_to_standard():
    raw = valid_spec()
    raw.pop("mode", None)
    assert validate_topic_spec(raw).mode == "standard"

def test_skill_validator_fills_missing_time_pair_from_fixed_now(tmp_path):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    raw["scope"].pop("end_time")
    result = run_validator(raw, "--default-now", "2026-07-21T12:00:00+08:00", "--default-days", "14")
    normalized = json.loads(result.stdout)
    assert normalized["scope"]["start_time"] == "2026-07-07T12:00:00+08:00"
    assert normalized["scope"]["end_time"] == "2026-07-21T12:00:00+08:00"

def test_skill_validator_rejects_only_one_time_boundary(tmp_path):
    raw = valid_spec()
    raw["scope"].pop("start_time")
    result = run_validator(raw, "--default-now", "2026-07-21T12:00:00+08:00", "--default-days", "14")
    assert result.returncode == 2
    assert "both start_time and end_time" in result.stderr
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: new tests FAIL because mode/default-time preparation is absent.

- [ ] **Step 3: Add optional mode to the canonical contract**

```python
@dataclass(frozen=True)
class TopicSpec:
    schema_version: int
    topic_name: str
    objective: str
    scope: TopicScope
    unit: str
    mode: str
    # existing criteria/examples/output fields follow

mode = raw.get("mode", "standard")
if mode not in {"standard", "exhaustive"}:
    raise ValueError("mode must be standard or exhaustive")
```

Add `mode` to schema properties but not its required list, preserving compatibility with existing specs. `TopicSpec.to_dict()` always emits the canonical value so run hashes distinguish modes.

- [ ] **Step 4: Add deterministic default-time preparation to the bundled validator**

Before schema validation, if both time fields are absent and both default arguments are present, set end to `--default-now` and start to exactly `default_days` earlier while preserving its timezone offset. If exactly one boundary is absent, reject it. The normalized JSON printed to stdout must always contain both timestamps.

- [ ] **Step 5: Run contract and validator tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Regenerate and verify the bundled schema**

Run:

```bash
python3 -m feedback_hub.topic_mining.contracts --write-schema codex-skills/mining-feedback-topics/references/topic-spec.schema.json
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py::test_skill_schema_matches_backend_contract -q
```

Expected: schema equality test PASS.

- [ ] **Step 7: Commit contract preparation**

```bash
git add feedback_hub/topic_mining/contracts.py feedback_hub/tests/test_topic_mining_contracts.py codex-skills/mining-feedback-topics/scripts/validate_topic_spec.py codex-skills/mining-feedback-topics/references/topic-spec.schema.json feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: add topic run modes and default time preparation"
```

### Task 2: Diverse 500-Candidate Classification Budget

**Files:**
- Modify: `feedback_hub/topic_mining/config.py`
- Create: `feedback_hub/topic_mining/diversity.py`
- Modify: `feedback_hub/topic_mining/retrieval.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Test: `feedback_hub/tests/test_topic_mining_diversity.py`
- Modify: `feedback_hub/tests/test_topic_mining_retrieval.py`
- Modify: `feedback_hub/tests/test_topic_mining_service.py`

**Interfaces:**
- Produces: `select_diverse_candidates(candidates, *, limit) -> Sequence[RecallHit]`.
- Produces mode-specific config fields `standard_channel_top_k=200`, `standard_recall_pool_limit=2000`, `standard_candidate_limit=500`, `exhaustive_channel_top_k=2000`, `exhaustive_recall_pool_limit=5000`, `exhaustive_candidate_limit=5000`, `standard_result_limit=100`, `review_page_limit=50`.
- Consumes each candidate's `ts_ms`, `channel`, `query_ids`, and `fused_rank`.

- [ ] **Step 1: Write failing diversity and budget tests**

```python
def test_selection_spreads_across_time_channel_and_semantic_query():
    rows = [
        recall("recent-wetype-a", ts=300, channel="wetype", query_ids=("positive:0",), rank=1),
        recall("recent-wetype-b", ts=299, channel="wetype", query_ids=("positive:0",), rank=2),
        recall("old-other", ts=100, channel="other", query_ids=("objective:0",), rank=20),
    ]
    selected = select_diverse_candidates(rows, limit=2)
    assert {row.item_id for row in selected} == {"recent-wetype-a", "old-other"}

def test_standard_service_classifies_at_most_five_hundred(topic_fixture):
    result = run_with_recall_count(topic_fixture, count=800, mode="standard")
    assert result.manifest["recall_pool_count"] == 800
    assert result.manifest["classified_count"] == 500
```

- [ ] **Step 2: Run diversity/retrieval/service tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_diversity.py feedback_hub/tests/test_topic_mining_retrieval.py feedback_hub/tests/test_topic_mining_service.py -q`

Expected: new tests FAIL because recall pool and classification limit are not separated.

- [ ] **Step 3: Add backend-owned budget configuration**

```python
standard_channel_top_k: int = 200
standard_recall_pool_limit: int = 2000
standard_candidate_limit: int = 500
exhaustive_channel_top_k: int = 2000
exhaustive_recall_pool_limit: int = 5000
exhaustive_candidate_limit: int = 5000
standard_result_limit: int = 100
review_page_limit: int = 50
```

Keep `classifier_batch_size=20`. Remove `bm25_top_k`, `vector_top_k`, and `candidate_limit` only after every caller and manifest test selects the replacement fields from `spec.mode`.

- [ ] **Step 4: Implement deterministic round-robin strata**

```python
def candidate_stratum(hit: RecallHit) -> tuple[int, str, str]:
    week_bucket = int(hit.item["ts_ms"]) // (7 * 24 * 60 * 60 * 1000)
    channel = str(hit.item.get("channel") or "")
    semantic_query = next((query_id for query_id in hit.query_ids if query_id != "bm25"), "bm25")
    return week_bucket, channel, semantic_query
```

Sort each stratum by `(fused_rank, item_id)`, order strata by their best fused rank, and take one item from each stratum per round until `limit`. This uses the best positive semantic query as the semantic bucket without adding a second clustering model.

- [ ] **Step 5: Preserve recall pool and select before classification**

`hybrid_recall()` chooses channel depth and recall-pool size from `spec.mode`; standard uses 200/2000 and exhaustive uses 2000/5000. In `service.py`, choose the classification limit from the same mode, call `select_diverse_candidates()`, persist `retrieved_candidate_count`, `recall_pool_count`, and `selected_candidate_count`, and pass only the selected sequence to `classify_candidates()`.

- [ ] **Step 6: Run focused tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_diversity.py feedback_hub/tests/test_topic_mining_retrieval.py feedback_hub/tests/test_topic_mining_service.py -q`

Expected: all tests PASS; standard mode never classifies item 501.

- [ ] **Step 7: Commit candidate budgeting**

```bash
git add feedback_hub/topic_mining/config.py feedback_hub/topic_mining/diversity.py feedback_hub/topic_mining/retrieval.py feedback_hub/topic_mining/service.py feedback_hub/tests/test_topic_mining_diversity.py feedback_hub/tests/test_topic_mining_retrieval.py feedback_hub/tests/test_topic_mining_service.py
git commit -m "feat: bound and diversify topic classification candidates"
```

### Task 3: Representative 100-Row Output and Scope Metadata

**Files:**
- Modify: `feedback_hub/topic_mining/diversity.py`
- Modify: `feedback_hub/topic_mining/export.py`
- Modify: `feedback_hub/topic_mining/service.py`
- Modify: `feedback_hub/tests/test_topic_mining_export.py`
- Modify: `feedback_hub/tests/test_topic_mining_end_to_end.py`

**Interfaces:**
- Produces: `select_representative_results(rows, recall_by_id, *, limit) -> list[dict]`.
- Produces manifest/API fields: `mode`, `result_scope`, `matched_total`, `returned_feedback`, `possibly_more_matches`.

- [ ] **Step 1: Write failing output-scope tests**

```python
def test_standard_export_limits_rows_and_reports_representative_scope(run_fixture):
    run_fixture.add_confirmed_matches(130)
    artifact, summary = export_run(run_fixture.run_id, "jsonl")
    assert len(read_jsonl(artifact)) == 100
    assert summary["matched_total"] == 130
    assert summary["returned_feedback"] == 100
    assert summary["result_scope"] == "representative"
    assert summary["possibly_more_matches"] is True

def test_standard_export_returns_all_when_under_limit(run_fixture):
    run_fixture.add_confirmed_matches(23)
    _, summary = export_run(run_fixture.run_id, "xlsx")
    assert summary["returned_feedback"] == 23
    assert summary["possibly_more_matches"] is False
```

- [ ] **Step 2: Run export/end-to-end tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_end_to_end.py -q`

Expected: new tests FAIL because every matched row is currently exported.

- [ ] **Step 3: Select representative rows without deleting full audit artifacts**

Use the same time/channel/semantic-query round-robin strata as candidate selection, but consider only final `matched` rows after overrides. Preserve `classified.jsonl`, `final_reviewed.jsonl`, and audit files unchanged; apply the 100-row limit only when building downloadable standard-mode artifacts.

- [ ] **Step 4: Persist explicit scope metadata**

```python
scope = {
    "mode": spec.mode,
    "result_scope": "representative" if spec.mode == "standard" and matched_total > len(export_rows) else "reviewed",
    "matched_total": matched_total,
    "returned_feedback": len(export_rows),
    "possibly_more_matches": spec.mode == "standard" and (retrieved_candidate_count > classified_count or matched_total > len(export_rows)),
}
```

Include this object in the run manifest, `get-run`, export response, and XLSX metadata sheet. Do not label a capped standard run `complete`; verification means internal artifact integrity, not exhaustive semantic coverage.

- [ ] **Step 5: Run export/end-to-end tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_end_to_end.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit representative output behavior**

```bash
git add feedback_hub/topic_mining/diversity.py feedback_hub/topic_mining/export.py feedback_hub/topic_mining/service.py feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_end_to_end.py
git commit -m "feat: export bounded representative topic results"
```

### Task 4: Policy Capabilities and 50-Row Review Pages

**Files:**
- Modify: `feedback_hub/topic_mining/api.py`
- Modify: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py`
- Modify: `feedback_hub/tests/test_topic_mining_api.py`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- `GET /capabilities` adds `default_time_days` and `run_modes` policy objects.
- `GET /runs/{run_id}/review-queue?offset=N&limit=M` returns at most 50 rows plus `total` and `next_offset`.
- Skill client command becomes `review-queue RUN_ID --output FILE [--offset N] [--limit 1..50]`.

- [ ] **Step 1: Write failing API/client paging tests**

```python
def test_capabilities_advertise_standard_policy(client):
    payload = client.get("/api/topic-mining/capabilities").json()
    assert payload["default_time_days"] == 14
    assert payload["run_modes"]["standard"] == {"candidate_limit": 500, "result_limit": 100}

def test_review_queue_is_capped_at_fifty(client, run_with_80_reviews):
    page = client.get(f"/api/topic-mining/runs/{run_with_80_reviews}/review-queue?offset=0&limit=80")
    assert page.status_code == 422
    first = client.get(f"/api/topic-mining/runs/{run_with_80_reviews}/review-queue?offset=0&limit=50").json()
    assert len(first["items"]) == 50
    assert first["next_offset"] == 50
```

- [ ] **Step 2: Run API/client tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: new tests FAIL because policies and pagination are absent.

- [ ] **Step 3: Advertise backend-owned defaults**

Add to capabilities:

```python
"default_time_days": 14,
"run_modes": {
    "standard": {"candidate_limit": config.standard_candidate_limit, "result_limit": config.standard_result_limit},
    "exhaustive": {"candidate_limit": config.exhaustive_candidate_limit, "result_limit": None},
},
"authentication": "internal_network_boundary",
```

- [ ] **Step 4: Add strict review pagination**

FastAPI query parameters must require `offset >= 0` and `1 <= limit <= 50`. Return `{run_id, items, total, next_offset}`. The client writes only the response page to the requested file and includes `--offset`/`--limit` in the query string.

- [ ] **Step 5: Run API/client tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit policy and paging interfaces**

```bash
git add feedback_hub/topic_mining/api.py codex-skills/mining-feedback-topics/scripts/topic_backend_client.py feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: expose topic budgets and paged review queues"
```

### Task 5: Update and Re-Validate the Distributable Skill

**Files:**
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/topic-spec.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Modify: `codex-skills/mining-feedback-topics/references/review-policy.md`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`
- Modify: `docs/superpowers/evals/mining-feedback-topics/scenarios.json`
- Modify: `docs/superpowers/evals/mining-feedback-topics/with-skill.md`

**Interfaces:**
- Consumes capabilities and paging from Task 4.
- Produces Skill behavior: last-two-weeks default, standard mode unless explicit completeness intent, page-wise review, representative-scope disclosure.

- [ ] **Step 1: Invoke required skill-writing discipline**

Before editing the Skill, read and follow `superpowers:writing-skills` and `skill-creator`. Record the new failure scenarios before changing `SKILL.md`.

- [ ] **Step 2: Add failing Skill behavior tests**

```python
def test_skill_defaults_missing_time_to_backend_advertised_two_weeks():
    body = skill_body()
    assert "default to the most recent 14 days" in body
    assert "ask the single time-range question" not in body

def test_skill_uses_exhaustive_only_for_explicit_completeness_intent():
    body = skill_body()
    assert "all, complete, or exhaustive" in body
    assert "mode: exhaustive" in body

def test_skill_discloses_representative_scope():
    body = skill_body()
    assert "possibly_more_matches" in body
    assert "representative" in body
```

- [ ] **Step 3: Run Skill package tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: new tests FAIL against the old missing-time question behavior.

- [ ] **Step 4: Rewrite only the affected Skill guidance**

`SKILL.md` must instruct the caller to:

1. Read capabilities and use its `default_time_days=14` when both times are missing.
2. Ask one time question only when exactly one boundary is supplied or timezone is unknowable.
3. Set `mode: standard` unless the user explicitly requests all/complete/exhaustive results.
4. Fetch review pages with limit 50 until `next_offset` is null.
5. State `result_scope`, `returned_feedback`, and `possibly_more_matches` at delivery.
6. Require only `FEEDBACK_TOPIC_API_URL`; describe `FEEDBACK_TOPIC_API_TOKEN` as unused for the current internal deployment.

Keep the Skill under its existing word-count gate and retain all original evidence, coverage, and no-direct-database rules.

- [ ] **Step 5: Update reference contracts and eval expectations**

Document canonical `mode`, default-time preparation command, capabilities policy fields, pagination, standard limits, exhaustive truncation, and representative output semantics. Add evaluation scenarios for no time, one-sided time, six-month standard output, and explicit exhaustive intent.

- [ ] **Step 6: Run Skill validation and focused backend regression**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py feedback_hub/tests/test_topic_mining_contracts.py feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_topic_mining_retrieval.py feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_end_to_end.py -q
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skills/mining-feedback-topics
```

Expected: all pytest tests PASS and `quick_validate.py` reports a valid Skill.

- [ ] **Step 7: Commit Skill policy updates**

```bash
git add codex-skills/mining-feedback-topics feedback_hub/tests/test_mining_feedback_topics_skill_package.py docs/superpowers/evals/mining-feedback-topics
git commit -m "feat: bound default topic mining skill runs"
```

### Task 6: Full Topic-Mining Regression and Real Read-Only Smoke Test

**Files:**
- No new source files; only ignored run artifacts may be created.

**Interfaces:**
- Consumes the deployed vector service and all Tasks 1–5.
- Produces verification evidence for a standard two-week run and an explicit six-month standard run.

- [ ] **Step 1: Run the complete runnable backend regression suite**

Run: `python3 -m pytest feedback_hub/tests -q --ignore=feedback_hub/tests/test_tagger_v2_feature_catalog.py --ignore=feedback_hub/tests/test_tagger_v2_feature_experiment.py --ignore=feedback_hub/tests/test_topic_lifecycle_matching.py --ignore=feedback_hub/tests/test_topic_model_routes.py`

Expected: all collected runnable tests PASS; the four known baseline import-error modules remain separately documented and are not silently counted as passing.

- [ ] **Step 2: Run a no-time standard request through the Skill client**

Use a fixed current timestamp in the generated spec preparation and submit a toolbar-style test topic without explicit time.

Expected: canonical run spec spans exactly 14 days, `mode=standard`, classification count is at most 500, and export count is at most 100.

- [ ] **Step 3: Run a six-month standard request against a controlled coverage fixture**

Use a copied test database whose coverage table spans six months and whose deterministic classifier fixture contains more than 100 matches. Expected: the backend honors the six-month hard scope but keeps classification/output budgets bounded; `result_scope=representative` and `possibly_more_matches=true` are present in API and workbook metadata. Separately verify that a real remote six-month request is rejected with an exact coverage gap unless a six-month raw-data backfill was explicitly approved and completed.

- [ ] **Step 4: Verify RRF never creates final labels**

Inspect `recall_candidates.jsonl`, `classified.jsonl`, and `final_reviewed.jsonl` for the smoke runs.

Expected: recall rows contain ranks/scores but no final label; every exported row has a classifier or review-override evidence chain.

- [ ] **Step 5: Record test commands and observed counts in the PR description**

Include exact test totals, run IDs, data cutoffs, classification counts, returned counts, vector watermark, and representative/truncated status. Do not commit real feedback text or smoke-test artifacts.

## Plan Acceptance Gate

- No-time requests resolve to exactly the latest 14 days without a user question.
- A one-sided time range still produces one direct clarification question.
- Standard mode classifies at most 500 candidates and exports at most 100 confirmed rows.
- Six-month standard runs remain bounded and disclose representative scope.
- Explicit exhaustive mode is distinct and reports truncation at its safety limit.
- Calling AI reviews only paginated boundary queues, not all recall candidates.
- Current internal deployment needs only the API URL and no token.
