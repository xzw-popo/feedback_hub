# Seven-Day Topic Shadow Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a resumable 2026-07-07 warm-up plus 2026-07-08 through 2026-07-14 sequential topic shadow run that pauses globally on the first explicit Knot quota signal.

**Architecture:** Add one quota-aware, incrementally scheduled model-call layer and route all three model stages through it. A new shadow-run orchestrator pulls each day, extracts conversation issue units, discovers daily topics, performs explicit first-day and cross-day lifecycle decisions, audits boundaries, and finalizes each day before advancing. The self-owned API is calibrated on 20 reviewed rows but is not authorized for automatic shadow-run fallback.

**Tech Stack:** Python 3.9+, pytest, requests, JSON/JSONL, NumPy, local Qwen3 embeddings, Knot agent API, OpenAI-compatible API, `openpyxl` for review workbooks.

## Global Constraints

- Run 2026-07-07 as `warm_up_only=true`; report-period dates are 2026-07-08 through 2026-07-14 inclusive.
- Use fresh complete local pulls with source links and deterministic media evidence; do not reuse partial daily experiment exports.
- Only user-authored messages can become evidence or issue units; service echoes and service-authored messages are excluded and counted by reason.
- Similarity recalls candidates only; the model decides every daily topic boundary.
- Every daily topic, including the first day with empty history, receives exactly one of `same_topic`, `new_topic`, `possible_subtopic`, `uncertain`, or `low_information`.
- A `low_information` topic retains evidence but never receives a stable `topic_id`.
- Knot agent A and B each use at most four concurrent requests.
- On the first explicit quota signal from either Knot route, stop scheduling all new work, let already in-flight requests finish, persist `paused_quota_exhausted`, and return control to the user.
- Do not continue on the other Knot route and do not send shadow-run work to the self-owned API without new user approval.
- Lifecycle batches contain at most five topics; one failed parse retry is allowed before splitting to batches of at most three.
- Credentials exist only in environment variables and never appear in model rows, manifests, reports, workbooks, logs, or commits.
- Preserve the completed 2026-07-12 to 2026-07-13 baseline and refinement artifacts.

---

### Task 1: Quota-Aware Model Routes and Incremental Scheduler

**Files:**
- Create: `feedback_hub/topic_discovery/model_routes.py`
- Create: `feedback_hub/tests/test_topic_model_routes.py`

**Interfaces:**
- Produces: `ModelRoute`, `ModelReply`, `QuotaExhaustedError`, `call_knot_route(...) -> ModelReply`, `call_openai_compatible_route(...) -> ModelReply`, and `run_pauseable_model_jobs(...) -> tuple[list[dict], dict]`.
- Consumes: stable jobs shaped as `(index: int, key: str, payload: dict)` and a worker shaped as `(index, key, payload, route) -> tuple[dict, str | None]`.
- Persists: `<output>.checkpoint.jsonl`, `<output>.failures.jsonl`, `<output>.run_state.json`, and finalized output only when coverage is complete.

- [ ] **Step 1: Write failing tests for explicit quota classification**

```python
from unittest.mock import Mock


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def test_call_knot_route_raises_typed_quota_without_retry() -> None:
    post = Mock(return_value=FakeResponse(429, {"error": "daily quota exceeded"}))
    route = ModelRoute("agent_a", "knot_agent", "https://a.test", "secret")

    with pytest.raises(QuotaExhaustedError) as caught:
        call_knot_route("prompt", route=route, http_post=post, max_retries=4)

    assert caught.value.route_name == "agent_a"
    assert post.call_count == 1
```

Also cover a non-200 top-level Knot error containing `次数上限`, a 503 that retries on the same route, and a 200 model response whose ordinary content mentions “额度” without being misclassified.

- [ ] **Step 2: Run the quota tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_model_routes.py -k quota -q`

Expected: FAIL because `feedback_hub.topic_discovery.model_routes` does not exist.

- [ ] **Step 3: Add typed route calls with sanitized provenance**

```python
@dataclass(frozen=True)
class ModelRoute:
    name: str
    endpoint_class: str
    api_url: str
    credential: str
    model: str = ""
    api_user: str = ""


class QuotaExhaustedError(RuntimeError):
    def __init__(self, route_name: str, status_code: int, response_excerpt: str):
        super().__init__(f"quota_exhausted:{route_name}:{status_code}")
        self.route_name = route_name
        self.status_code = status_code
        self.response_excerpt = response_excerpt[:200]


@dataclass(frozen=True)
class ModelReply:
    content: str
    route_name: str
    endpoint_class: str
    model: str
    attempts: int
    elapsed_ms: int
    retry_chain: tuple[str, ...]
```

`call_knot_route` classifies HTTP 429 or a top-level API error containing one of `quota`, `rate limit`, `次数上限`, `额度已用完`, or `超过限额` as explicit quota. It retries connection errors and HTTP 502/503/504 on the same route. It must not scan normal model content for quota markers.

`call_openai_compatible_route` posts a system and user message to the configured chat-completions endpoint and returns the same provenance shape. It exists for the canary only in this plan.

- [ ] **Step 4: Write failing scheduler tests for global pause and resume**

```python
def test_pauseable_scheduler_stops_new_work_after_first_quota(tmp_path) -> None:
    jobs = [(index, f"j{index}", {"value": index}) for index in range(10)]
    seen = []

    def worker(index, key, payload, route):
        seen.append(key)
        if key == "j2":
            raise QuotaExhaustedError(route.name, 429, "quota")
        return {"key": key, "route_source": route.name}, None

    rows, stats = run_pauseable_model_jobs(
        jobs, worker, routes=[route_a, route_b], output_path=tmp_path / "rows.jsonl",
        concurrency_per_route=1,
    )

    assert stats["run_status"] == "paused_quota_exhausted"
    assert stats["quota_route"] in {"agent_a", "agent_b"}
    assert len(seen) < len(jobs)
    assert not (tmp_path / "rows.jsonl").exists()
```

Add a resume test proving that validated checkpoint rows are skipped and that a completed run writes output exactly once in input order.

- [ ] **Step 5: Run scheduler tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_model_routes.py -k 'scheduler or resume' -q`

Expected: FAIL because the pauseable scheduler is missing.

- [ ] **Step 6: Add incremental scheduling and run-state persistence**

Schedule at most `len(routes) * concurrency_per_route` futures at a time. Submit a replacement only after one future completes. On `QuotaExhaustedError`, set a shared stop event, submit no replacements, wait for existing futures, append their validated records, and write:

```json
{
  "run_status": "paused_quota_exhausted",
  "quota_route": "agent_a",
  "completed": 2,
  "remaining": 8
}
```

Persist response excerpts only after applying `_redact_secrets`; do not persist route credentials or authorization headers.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_model_routes.py -q`

Expected: all model-route tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add feedback_hub/topic_discovery/model_routes.py feedback_hub/tests/test_topic_model_routes.py
git commit -m "feat: pause topic model jobs on agent quota"
```

### Task 2: Connect All Model Stages to the Pauseable Scheduler

**Files:**
- Modify: `feedback_hub/topic_discovery/conversation_runner.py`
- Modify: `feedback_hub/data/topic_discovery_prototype/run_daily_topic_discovery.py`
- Modify: `feedback_hub/topic_discovery/lifecycle_matching.py`
- Modify: `feedback_hub/tests/test_topic_discovery_conversation_runner.py`
- Modify: `feedback_hub/tests/test_topic_discovery_runner.py`
- Modify: `feedback_hub/tests/test_topic_lifecycle_matching.py`

**Interfaces:**
- Consumes: `ModelRoute` and `run_pauseable_model_jobs` from Task 1.
- Produces: each model row records `route_source`, `endpoint_class`, `model`, `prompt_version`, `attempts`, `retry_chain`, `elapsed_ms`, parse status, and terminal error class.
- Returns: each stage summary includes `run_status`; callers must not finalize a paused stage.

- [ ] **Step 1: Write failing integration tests for route provenance and pause propagation**

```python
def test_conversation_runner_propagates_quota_pause(tmp_path) -> None:
    def fake_call(prompt, *, route, **kwargs):
        raise QuotaExhaustedError(route.name, 429, "quota")

    rows, stats = run_conversations_multi_channel(
        [conversation_pack], routes=[route_a], output_path=tmp_path / "rows.jsonl",
        concurrency_per_route=1, call_fn=fake_call,
    )

    assert rows == []
    assert stats["run_status"] == "paused_quota_exhausted"
```

Add equivalent tests for daily clustering and lifecycle matching. A paused stage must not write a finalized output or fabricate parsed rows.

- [ ] **Step 2: Run the focused stage tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_discovery_conversation_runner.py feedback_hub/tests/test_topic_discovery_runner.py feedback_hub/tests/test_topic_lifecycle_matching.py -k 'quota or provenance' -q`

Expected: failures because the three runners still submit all jobs through `run_incremental_jobs`.

- [ ] **Step 3: Replace duplicated route scheduling in all three stages**

Each worker calls the route adapter supplied by the scheduler and returns a row with sanitized provenance:

```python
row.update({
    "route_source": reply.route_name,
    "endpoint_class": reply.endpoint_class,
    "model": reply.model or "agent_default",
    "prompt_version": prompt_version,
    "attempts": reply.attempts,
    "retry_chain": list(reply.retry_chain),
    "elapsed_ms": reply.elapsed_ms,
})
```

Delete `knot_route` from newly generated model rows after keeping reader compatibility for historical artifacts.

- [ ] **Step 4: Tighten lifecycle prompt formatting and parse recovery**

Update `build_lifecycle_match_prompt` so the output example is plain JSON rather than a Markdown code fence and add these exact rules:

```text
Return exactly one legal JSON object and decide every supplied daily_topic_id once.
Do not use literal ASCII double quotes inside reason text; use Chinese corner quotes 「」 when quotation is necessary.
Never invent a daily_topic_id or historical_topic_id.
```

Set lifecycle `max_batch_size=5`. Retry one parse failure with the same batch, then split unresolved work into batches of at most three. Keep strict `json.loads` and exact coverage; do not add loose JSON repair or an `irrelevant_invalid` fallback.

- [ ] **Step 5: Run the three stage suites and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_discovery_conversation_runner.py feedback_hub/tests/test_topic_discovery_runner.py feedback_hub/tests/test_topic_lifecycle_matching.py -q`

Expected: all stage tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add feedback_hub/topic_discovery/conversation_runner.py feedback_hub/data/topic_discovery_prototype/run_daily_topic_discovery.py feedback_hub/topic_discovery/lifecycle_matching.py feedback_hub/tests/test_topic_discovery_conversation_runner.py feedback_hub/tests/test_topic_discovery_runner.py feedback_hub/tests/test_topic_lifecycle_matching.py
git commit -m "feat: route topic stages through pauseable model calls"
```

### Task 3: Explicit First-Day Eligibility and Cross-Day Low-Information Audit

**Files:**
- Modify: `feedback_hub/topic_discovery/lifecycle.py`
- Modify: `feedback_hub/topic_discovery/lifecycle_matching.py`
- Modify: `feedback_hub/topic_discovery/refinement.py`
- Modify: `feedback_hub/tests/test_topic_lifecycle.py`
- Modify: `feedback_hub/tests/test_topic_lifecycle_matching.py`
- Modify: `feedback_hub/tests/test_topic_refinement.py`

**Interfaces:**
- Produces: `match_daily_topics` requires explicit decisions even when history is empty.
- Produces: `audit_low_information_upgrades(current_topics, prior_low_information, current_embeddings, prior_embeddings, threshold=0.78) -> list[dict]`.
- Preserves: `seed_topic_store` only for reading historical experiments; the shadow runner must not call it.

- [ ] **Step 1: Write failing first-day lifecycle tests**

```python
def test_empty_history_requires_explicit_eligibility_decisions() -> None:
    decisions = [
        {"daily_topic_id": "d1", "verdict": "new_topic", "historical_topic_id": None},
        {"daily_topic_id": "d2", "verdict": "low_information", "historical_topic_id": None},
    ]

    events = match_daily_topics([topic_d1, topic_d2], [], decisions)

    assert [event["event_type"] for event in events] == ["topic_created", "low_information_held"]
    assert events[1]["topic_id"] is None
```

Also assert that `decisions=None` fails on empty history and that the candidate plan emits model batches with zero historical candidates.

- [ ] **Step 2: Run first-day tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_lifecycle.py feedback_hub/tests/test_topic_lifecycle_matching.py -k 'empty_history or first_day' -q`

Expected: FAIL because `match_daily_topics` currently calls `seed_topic_store` when history is empty.

- [ ] **Step 3: Remove automatic first-day seeding from the shadow path**

Validate exact decision coverage before checking history. For empty history, accept only `new_topic` and `low_information`; reject any historical ID. Allocate stable IDs only for `new_topic` events and apply events through the normal store transition.

- [ ] **Step 4: Write failing possible-upgrade audit tests**

```python
def test_low_information_upgrade_audit_flags_similarity_without_merging() -> None:
    rows = audit_low_information_upgrades(
        [eligible_topic], [held_row],
        np.asarray([[1.0, 0.0]], dtype="float32"),
        np.asarray([[0.8, 0.6]], dtype="float32"), threshold=0.78,
    )

    assert rows[0]["audit_flag"] == "possible_low_information_upgrade"
    assert rows[0]["prior_daily_topic_id"] == held_row["daily_topic_id"]
    assert "topic_id" not in rows[0]
```

- [ ] **Step 5: Add deterministic low-information recall audit**

Normalize both matrices, compute cosine similarity, emit every pair at or above 0.78 in deterministic descending-similarity order, and preserve both sides' evidence links. Do not mutate either topic collection.

- [ ] **Step 6: Run Task 3 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_lifecycle.py feedback_hub/tests/test_topic_lifecycle_matching.py feedback_hub/tests/test_topic_refinement.py -q`

Expected: all lifecycle and refinement tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add feedback_hub/topic_discovery/lifecycle.py feedback_hub/topic_discovery/lifecycle_matching.py feedback_hub/topic_discovery/refinement.py feedback_hub/tests/test_topic_lifecycle.py feedback_hub/tests/test_topic_lifecycle_matching.py feedback_hub/tests/test_topic_refinement.py
git commit -m "feat: require first-day topic eligibility decisions"
```

### Task 4: Daily Input Reconciliation and Author Filtering

**Files:**
- Modify: `feedback_hub/tagger/v2/full_daily_input.py`
- Modify: `scripts/build_full_daily_input.py`
- Modify: `feedback_hub/tests/test_full_daily_input.py`

**Interfaces:**
- Produces: `extract_full_daily_rows(...) -> (rows, stats)` with raw, included, and excluded counts reconciled by reason.
- Produces: `validate_daily_input(rows, stats) -> dict` and fails before model calls on duplicate feedback IDs or count mismatch.

- [ ] **Step 1: Write failing reconciliation tests**

```python
def test_full_daily_input_reconciles_user_and_excluded_messages() -> None:
    rows, stats = extract_full_daily_rows(response_with_user_service_echo_and_agent_reply, **window)

    assert [row["feedback_id"] for row in rows] == ["user-1"]
    assert stats["raw_messages_in_window"] == 3
    assert stats["included_user_messages"] == 1
    assert stats["excluded_by_reason"] == {
        "service_authored": 1,
        "service_echo": 1,
    }
    assert stats["raw_messages_in_window"] == stats["included_user_messages"] + sum(stats["excluded_by_reason"].values())
```

Add tests for unknown sender, unsupported type, missing feedback ID, duplicate feedback ID, source link count, and media count.

- [ ] **Step 2: Run input tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_full_daily_input.py -q`

Expected: failures because the current stats do not reconcile every in-window exclusion reason.

- [ ] **Step 3: Add explicit inclusion and exclusion accounting**

Count every in-window raw message once. Include only `sender == 0`, supported text/image/video types, non-echo text, and nonempty feedback IDs. Record excluded rows under stable values:

```python
EXCLUSION_REASONS = {
    "service_authored",
    "unknown_author",
    "unsupported_type",
    "outside_window",
    "service_echo",
    "missing_feedback_id",
}
```

`outside_window` is reported separately and excluded from the in-window reconciliation denominator. Preserve `external_chat_url`, media URL availability, media type, and author role on every included row.

- [ ] **Step 4: Add CLI validation before database and JSONL writes**

`scripts/build_full_daily_input.py` calls `validate_daily_input` and writes its result under `input_summary.json["reconciliation"]`. A validation failure exits nonzero without starting any model stage.

- [ ] **Step 5: Run Task 4 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_full_daily_input.py feedback_hub/tests/test_puller.py -q`

Expected: all pull and full-daily-input tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add feedback_hub/tagger/v2/full_daily_input.py scripts/build_full_daily_input.py feedback_hub/tests/test_full_daily_input.py feedback_hub/tests/test_puller.py
git commit -m "feat: reconcile daily user feedback inputs"
```

### Task 5: Review Set and Self-Owned API Canary

**Files:**
- Create: `feedback_hub/topic_discovery/calibration.py`
- Create: `scripts/run_topic_shadow_calibration.py`
- Create: `feedback_hub/tests/test_topic_shadow_calibration.py`

**Interfaces:**
- Produces: `select_boundary_review_rows(...) -> list[dict]`, `select_lifecycle_canary_rows(...) -> list[dict]`, and `evaluate_lifecycle_canary(...) -> dict`.
- Reads: the completed 2026-07-12 to 2026-07-13 refinement artifacts and reviewed decisions.
- Writes: `boundary_review.jsonl`, `boundary_review.xlsx`, `self_api_canary_rows.jsonl`, `self_api_canary_results.jsonl`, and `self_api_canary_summary.json`.

- [ ] **Step 1: Write failing deterministic selection tests**

```python
def test_canary_selection_covers_all_verdicts_and_history_minimum() -> None:
    rows = select_lifecycle_canary_rows(reviewed_rows, size=20)

    verdicts = Counter(row["reviewed_verdict"] for row in rows)
    assert len(rows) == 20
    assert all(verdicts[name] >= 2 for name in ALLOWED_VERDICTS)
    assert sum(bool(row.get("reviewed_historical_topic_id")) for row in rows) >= 5
    assert {bool(row.get("has_media_evidence")) for row in rows} == {False, True}
```

Also assert the boundary review contains all 39 priority rows plus 20 deterministic strata from the 50 high-similarity-new pool without duplicate daily topic IDs.

- [ ] **Step 2: Run selection tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_shadow_calibration.py -k selection -q`

Expected: FAIL because calibration helpers do not exist.

- [ ] **Step 3: Add deterministic review and canary selection**

Sort by stable daily topic ID inside strata. Use similarity bands `<0.82`, `0.82-0.90`, and `>0.90`, then platform, feedback type, and media presence. Reject source data that cannot satisfy the canary coverage contract instead of silently weakening it.

- [ ] **Step 4: Write failing canary evaluation tests**

```python
def test_canary_pass_requires_both_thresholds() -> None:
    summary = evaluate_lifecycle_canary(results_with_17_verdict_matches_and_4_of_5_history_matches)
    assert summary["verdict_exact"] == {"matched": 17, "total": 20, "rate": 0.85}
    assert summary["history_exact"]["rate"] == 0.8
    assert summary["eligible_for_future_fallback"] is True
```

Add failures for 16/20 verdict agreement, history agreement below 80%, parse failure, missing result, and invented ID.

- [ ] **Step 5: Add OpenAI-compatible canary execution**

Read `LLM_API_URL`, `LLM_API_KEY`, and `LLM_MODEL` from the environment. Reuse `build_lifecycle_match_prompt` and `parse_lifecycle_match_reply`. Use the generic system prompt already approved for the agents. Write only endpoint class and model name to artifacts; never write URL or API key.

- [ ] **Step 6: Build the human review workbook**

Use `openpyxl` to create `boundary_review.xlsx` with frozen headers, filters, wrapped text, stable column widths, clickable source links, Codex result/reason columns, and blank user result/reason columns only for genuinely ambiguous rows.

- [ ] **Step 7: Run Task 5 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_shadow_calibration.py feedback_hub/tests/test_topic_lifecycle_matching.py -q`

Expected: all calibration and parser tests pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add feedback_hub/topic_discovery/calibration.py scripts/run_topic_shadow_calibration.py feedback_hub/tests/test_topic_shadow_calibration.py
git commit -m "feat: calibrate topic lifecycle fallback channel"
```

### Task 6: Sequential Shadow-Run Orchestrator and Seven-Day Outputs

**Files:**
- Create: `feedback_hub/topic_discovery/shadow_run.py`
- Create: `scripts/run_topic_shadow.py`
- Create: `feedback_hub/tests/test_topic_shadow_run.py`
- Modify: `scripts/run_topic_conversations.py`
- Modify: `scripts/run_topic_lifecycle_match.py`

**Interfaces:**
- Produces: `ShadowRunConfig`, `run_shadow_day(config, date, prior_state) -> ShadowDayResult`, `run_shadow_period(config, *, day_runner=run_shadow_day) -> dict`, and `summarize_shadow_period(...) -> dict`.
- Uses: daily input builder, conversation runner, daily discovery, lifecycle matching, boundary audit, and the pauseable model scheduler.
- Writes: `run_state.json`, per-day directories, seven-day JSON/Markdown summaries, and a source-linked XLSX workbook.

- [ ] **Step 1: Write failing orchestration tests for strict date order**

```python
def test_shadow_period_uses_warmup_memory_and_reports_only_seven_days(tmp_path) -> None:
    calls = []

    def fake_day_runner(config, current_date, prior_state):
        calls.append((current_date.isoformat(), prior_state))
        return ShadowDayResult.finalized(
            current_date=current_date,
            topic_store=[{"topic_id": f"topic:{current_date.day}"}],
        )

    config = ShadowRunConfig(
        start_date=date(2026, 7, 7),
        end_date=date(2026, 7, 9),
        report_start_date=date(2026, 7, 8),
        input_root=tmp_path / "input",
        output_root=tmp_path / "run",
        review_output=tmp_path / "review",
    )
    summary = run_shadow_period(config, day_runner=fake_day_runner)

    assert summary["processed_dates"] == ["2026-07-07", "2026-07-08", "2026-07-09"]
    assert summary["report_dates"] == ["2026-07-08", "2026-07-09"]
    assert summary["days"][0]["warm_up_only"] is True
    assert calls[1][1].topic_store == [{"topic_id": "topic:7"}]
```

Add tests proving a failed or paused day prevents the next date from starting and that resume restarts the blocked stage rather than rebuilding completed prior stages.

- [ ] **Step 2: Run orchestrator tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_shadow_run.py -q`

Expected: FAIL because the orchestrator does not exist.

- [ ] **Step 3: Add day-level state machine**

Use these terminal and resumable states:

```python
STAGES = (
    "input_ready",
    "conversations_ready",
    "daily_topics_ready",
    "lifecycle_ready",
    "audit_ready",
    "day_finalized",
)
RUN_STATUSES = {"running", "paused_quota_exhausted", "failed", "completed"}
```

Write `run_state.json` atomically after every stage. A day writes its next-day topic store only after exact conversation, cluster, and lifecycle coverage checks pass.

- [ ] **Step 4: Add first-day and subsequent-day lifecycle orchestration**

For 2026-07-07, create an empty historical matrix with the same embedding width, recall zero candidates, run eligibility decisions, and apply events to an empty store. For later days, consume the prior finalized store and embeddings. Never call `seed_topic_store` in the shadow runner.

- [ ] **Step 5: Add daily audit and appendix outputs**

Each finalized day writes:

```text
input_summary.json
conversation_packs.jsonl
conversation_issue_units.jsonl
daily_topics.jsonl
lifecycle_candidates.jsonl
lifecycle_decisions.jsonl
topic_events.jsonl
topic_store.jsonl
low_information_pool.jsonl
media_appendix.jsonl
boundary_audit.jsonl
possible_low_information_upgrades.jsonl
summary.json
manifest.json
shadow_report.md
```

Manifests hash every finalized artifact and reject settings whose key contains `token`, `secret`, or `api_key`.

- [ ] **Step 6: Add seven-day summary and review workbook**

Aggregate only 2026-07-08 through 2026-07-14. Reconcile daily and total counts for inputs, conversations, issue units, verdicts, media appendix, audits, retries, quota signals, and route share. The workbook includes Daily Summary, Topic Events, Boundary Audit, Low Information, Media Appendix, and Route Usage sheets with clickable feedback URLs.

- [ ] **Step 7: Run Task 6 tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_shadow_run.py feedback_hub/tests/test_topic_discovery_runner.py feedback_hub/tests/test_topic_lifecycle_matching.py -q`

Expected: all shadow orchestration and dependent stage tests pass.

- [ ] **Step 8: Commit Task 6**

```bash
git add feedback_hub/topic_discovery/shadow_run.py scripts/run_topic_shadow.py feedback_hub/tests/test_topic_shadow_run.py scripts/run_topic_conversations.py scripts/run_topic_lifecycle_match.py
git commit -m "feat: orchestrate sequential topic shadow runs"
```

### Task 7: Verification, Calibration, and Live Shadow Run

**Files:**
- No production-code files unless verification finds a defect; any defect fix follows a new failing regression test and its own focused commit.
- Runtime data: `feedback_hub/data/topic_shadow_run_20260707_20260714/v1/` (gitignored).
- Review outputs: `outputs/topic_shadow_run_20260708_20260714/` (do not stage).

**Interfaces:**
- Consumes: environment-only Knot route credentials and self-owned API configuration.
- Produces: either a completed seven-day shadow result or a resumable `paused_quota_exhausted` checkpoint report.

- [ ] **Step 1: Run focused and full regression tests**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_model_routes.py \
  feedback_hub/tests/test_topic_discovery_conversation_runner.py \
  feedback_hub/tests/test_topic_discovery_runner.py \
  feedback_hub/tests/test_topic_lifecycle.py \
  feedback_hub/tests/test_topic_lifecycle_matching.py \
  feedback_hub/tests/test_topic_refinement.py \
  feedback_hub/tests/test_full_daily_input.py \
  feedback_hub/tests/test_topic_shadow_calibration.py \
  feedback_hub/tests/test_topic_shadow_run.py -q
python3 -m pytest feedback_hub/tests -q
```

Expected: both commands pass with zero failures.

- [ ] **Step 2: Scan tracked code and planned reports for credentials**

Run:

```bash
git grep -nE '(x-knot-api-token|KNOT_API_TOKEN|LLM_API_KEY|Authorization).*(=|:).*[A-Za-z0-9]{16,}' -- . ':!feedback_hub/data' ':!outputs'
```

Expected: no credential value match.

- [ ] **Step 3: Generate and review the 59-row boundary workbook**

Run:

```bash
python3 scripts/run_topic_shadow_calibration.py boundary-review \
  --refinement-dir feedback_hub/data/topic_discovery_prototype/refinement_20260712_20260713 \
  --output-dir outputs/topic_shadow_run_20260708_20260714
```

Expected: 39 priority rows plus 20 stratified high-similarity-new rows, no duplicate topic ID, and all clear Codex-review columns populated.

- [ ] **Step 4: Run and evaluate the 20-row self-owned API canary**

Run:

```bash
python3 scripts/run_topic_shadow_calibration.py self-api-canary \
  --refinement-dir feedback_hub/data/topic_discovery_prototype/refinement_20260712_20260713 \
  --output-dir outputs/topic_shadow_run_20260708_20260714
```

Expected: exactly 20 parsed decisions and a summary stating whether verdict agreement is at least 85% and historical-topic agreement is at least 80%. This command does not authorize automatic fallback.

- [ ] **Step 5: Pull and validate all eight dates**

Run:

```bash
for day in 2026-07-07 2026-07-08 2026-07-09 2026-07-10 2026-07-11 2026-07-12 2026-07-13 2026-07-14; do
  python3 scripts/build_full_daily_input.py \
    --date "$day" \
    --output-dir "feedback_hub/data/topic_shadow_run_20260707_20260714/v1/input_${day//-/}"
done
```

Expected: every `input_summary.json` has `reconciliation.valid=true`, no duplicate feedback IDs, and nonnegative source-link/media counts.

- [ ] **Step 6: Start the sequential run with quota pause enabled**

Run:

```bash
python3 scripts/run_topic_shadow.py \
  --start-date 2026-07-07 \
  --end-date 2026-07-14 \
  --report-start-date 2026-07-08 \
  --input-root feedback_hub/data/topic_shadow_run_20260707_20260714/v1 \
  --output-root feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run \
  --review-output outputs/topic_shadow_run_20260708_20260714 \
  --concurrency-per-knot-route 4 \
  --pause-on-first-quota \
  --resume
```

Expected outcomes:

- `completed`: all dates finalize and the seven-day summary/workbook reconcile.
- `paused_quota_exhausted`: the process exits cleanly after in-flight calls, writes the route name, completed request count, current date/stage, and remaining work, and then stops. Do not resume or use the self-owned API until the user decides.

- [ ] **Step 7: Report the live checkpoint to the user**

For a quota pause, report the explicit quota evidence without credentials, agent route, date and stage, total completed/failed/remaining jobs, in-flight completions, and exact resume command. For completion, report seven-day totals, unresolved audit/review counts, route shares, workbook path, and verification commands.

- [ ] **Step 8: Commit only regression fixes, not runtime artifacts**

Run:

```bash
git status --short
git diff --check
```

Expected: no runtime data, output workbook, raw response, checkpoint, token, or API key is staged. Commit a regression fix only with its test and focused source files.
