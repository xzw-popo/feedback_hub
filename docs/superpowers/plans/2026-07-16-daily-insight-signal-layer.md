# Daily Insight Signal Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline 2026-07-14 daily insight experiment that turns the existing seven-day atomic topic artifacts into auditable report candidates, at most five main insights, a media appendix, and a review workbook with source links.

**Architecture:** Add a deterministic signal-feature and broad-recall layer on top of the existing topic shadow artifacts, then use one topic-level LLM editor stage to split or combine related candidates and decide `main`, `observe`, `exclude`, or `manual_review`. Keep report grouping isolated from stable topic memory, reuse the existing quota-aware multi-route scheduler, and render JSONL, Markdown, and XLSX artifacts from validated decisions.

**Tech Stack:** Python 3.9+, pytest, NumPy, openpyxl, JSON/JSONL, existing local topic embeddings, existing `feedback_hub.topic_discovery.model_routes` multi-route runner.

## Global Constraints

- The first experiment report date is exactly `2026-07-14`; the baseline is `2026-07-07` through `2026-07-13`.
- Counts are deduplicated by `conversation_id`, never by message count.
- The 404 report-day atomic topics must each appear exactly once in the deterministic feature output.
- Existing `report_candidate` and message-level value labels are not recall or admission gates.
- Feature policy `不进入报告` is a hard exclusion only when every known member feature has that policy; `进入报告` only grants eligibility.
- Report-layer grouping must not modify `topic_store.jsonl` or write lifecycle events.
- A `new_topic` lifecycle verdict does not by itself prove a product-level new issue.
- A `rising` claim is legal only when deterministic history features authorize it.
- Main report output has zero to five items; it must not fill unused slots.
- Media-bearing text-insufficient feedback stays in a separate appendix and does not compete with main insights.
- Every main insight must retain at least one clickable source conversation URL.
- Model output failures remain explicit; there is no semantic fallback label.
- Persist model route provenance, prompt version, retries, parse status, and elapsed time, but never credentials.
- Do not rerun conversation extraction, daily clustering, or lifecycle matching for this experiment.

---

### Task 1: Build deterministic topic timelines and signal features

**Files:**
- Create: `feedback_hub/topic_discovery/report_signals.py`
- Test: `feedback_hub/tests/test_topic_report_signals.py`

**Interfaces:**
- Consumes: per-day `daily_topics`, `lifecycle_decisions`, `issue_units`, final `topic_store`, and `FeatureCatalog`.
- Produces: `build_topic_signal_features(days, final_topic_store, catalog, *, report_date) -> list[dict[str, Any]]`.
- Produces: `validate_feature_coverage(features, expected_daily_topic_ids) -> None`.
- Each feature row contains `daily_topic_id`, `stable_topic_id`, `today_conversation_count`, seven `baseline_daily_counts`, lifecycle fields, evidence aggregates, policy action, and `allowed_trend_claims`.

- [ ] **Step 1: Write failing timeline and coverage tests**

```python
def test_build_topic_signal_features_counts_unique_conversations_and_history():
    days = [
        _day("2026-07-13", [_topic("d13", ["c1", "c1", "c2"])], [_decision("d13", "new_topic")]),
        _day("2026-07-14", [_topic("d14", ["c2", "c3"])], [_decision("d14", "same_topic", "t1")]),
    ]
    store = [{"topic_id": "t1", "source_daily_topic_ids": ["d13", "d14"]}]

    rows = build_topic_signal_features(days, store, _catalog(), report_date="2026-07-14")

    assert rows[0]["today_conversation_count"] == 2
    assert rows[0]["baseline_daily_counts"][-1] == 2
    assert rows[0]["stable_topic_id"] == "t1"
    assert rows[0]["source_links"] == ["https://feedback/c2", "https://feedback/c3"]


def test_validate_feature_coverage_rejects_missing_and_duplicate_topics():
    with pytest.raises(ValueError, match="exact coverage"):
        validate_feature_coverage(
            [{"daily_topic_id": "d1"}, {"daily_topic_id": "d1"}],
            {"d1", "d2"},
        )
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_report_signals.py -q`

Expected: FAIL because `feedback_hub.topic_discovery.report_signals` does not exist.

- [ ] **Step 3: Implement topic-ID mapping, daily timelines, evidence aggregates, and coverage validation**

```python
def build_topic_signal_features(days, final_topic_store, catalog, *, report_date):
    report_day = _unique_day(days, report_date)
    topic_id_by_daily_id = {
        daily_id: str(topic["topic_id"])
        for topic in final_topic_store
        for daily_id in topic.get("source_daily_topic_ids") or []
    }
    day_topic_by_id = {
        str(topic["daily_topic_id"]): (day["date"], topic)
        for day in days
        for topic in day["daily_topics"]
    }
    features = []
    for topic in report_day["daily_topics"]:
        daily_topic_id = str(topic["daily_topic_id"])
        stable_topic_id = topic_id_by_daily_id.get(daily_topic_id)
        source_daily_ids = _source_daily_ids(final_topic_store, stable_topic_id, daily_topic_id)
        daily_counts = _daily_conversation_counts(source_daily_ids, day_topic_by_id)
        member_units = _topic_issue_units(topic, report_day["issue_units"])
        policy = _resolve_feature_policy(member_units, catalog)
        features.append({
            "daily_topic_id": daily_topic_id,
            "stable_topic_id": stable_topic_id,
            "title": str(topic.get("title") or ""),
            "description": str(topic.get("description") or ""),
            "today_conversation_count": len(set(topic.get("conversation_ids") or [])),
            "baseline_daily_counts": [daily_counts.get(day, 0) for day in _baseline_dates(report_date, 7)],
            "source_links": _dedupe(link for unit in member_units for link in unit.get("evidence_links") or []),
            "feature_policy": policy,
            **_evidence_aggregates(topic, member_units),
        })
    validate_feature_coverage(features, {str(row["daily_topic_id"]) for row in report_day["daily_topics"]})
    return sorted(features, key=lambda row: row["daily_topic_id"])
```

Implementation must also validate unique day dates, unique daily topic IDs, unique issue-unit IDs, valid report date ordering, and stable-topic references. An absent stable topic ID remains explicit for `low_information` or unresolved cases.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_report_signals.py -q`

Expected: PASS.

- [ ] **Step 5: Commit deterministic feature extraction**

```bash
git add feedback_hub/topic_discovery/report_signals.py feedback_hub/tests/test_topic_report_signals.py
git commit -m "feat: derive daily topic signal features"
```

---

### Task 2: Add broad recall routes and deterministic trend authorization

**Files:**
- Modify: `feedback_hub/topic_discovery/report_signals.py`
- Modify: `feedback_hub/tests/test_topic_report_signals.py`

**Interfaces:**
- Consumes: feature rows from Task 1.
- Produces: `recall_report_candidates(features) -> list[dict[str, Any]]`.
- Produces: `classify_allowed_trend_claims(today_count, baseline_counts, active_dates) -> list[str]`.
- Candidate rows add `recall_reasons`, `candidate_band`, `allowed_trend_claims`, and `deterministic_exclusion`.

- [ ] **Step 1: Write failing tests for every recall path and policy behavior**

```python
@pytest.mark.parametrize("reason,row", [
    ("repeat_today", _feature(today=2)),
    ("cross_day_persistent", _feature(today=1, baseline=[1, 0, 1, 0, 0, 1, 0])),
    ("clear_new", _feature(today=1, verdict="new_topic", confidence=0.9, evidence_span_count=1)),
    ("demand_opportunity", _feature(today=1, feedback_type_counts={"feature_request": 1})),
    ("high_value_single", _feature(today=1, feedback_type_counts={"bug_problem": 1}, evidence_span_count=2, known_context=True)),
])
def test_recall_report_candidates_uses_independent_routes(reason, row):
    candidate = recall_report_candidates([row])[0]
    assert reason in candidate["recall_reasons"]


def test_no_report_policy_excludes_only_unanimous_known_features():
    excluded = recall_report_candidates([_feature(feature_policy={"action": "exclude", "known": 2, "unknown": 0})])[0]
    mixed = recall_report_candidates([_feature(feature_policy={"action": "review", "known": 1, "unknown": 1})])[0]
    assert excluded["deterministic_exclusion"] == "feature_policy_excluded"
    assert mixed["deterministic_exclusion"] is None


def test_rising_requires_nonzero_history_and_supported_delta():
    assert "rising" not in classify_allowed_trend_claims(3, [0, 0, 0, 0, 0, 0, 0], [])
    assert "new_signal" in classify_allowed_trend_claims(3, [0, 0, 0, 0, 0, 0, 0], [])
    assert "rising" in classify_allowed_trend_claims(5, [1, 1, 0, 1, 1, 0, 1], ["2026-07-07"])
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_report_signals.py -q`

Expected: FAIL because recall and trend functions do not exist.

- [ ] **Step 3: Implement independent recall reasons without a composite importance score**

```python
def classify_allowed_trend_claims(today_count, baseline_counts, active_dates):
    claims = {"none"}
    nonzero_days = sum(value > 0 for value in baseline_counts)
    baseline_mean = sum(baseline_counts) / len(baseline_counts) if baseline_counts else 0.0
    if today_count >= 2:
        claims.add("repeated")
    if nonzero_days >= 2:
        claims.add("persistent")
    if any(baseline_counts) and not any(baseline_counts[-2:]) and today_count:
        claims.add("reappeared")
    if not any(baseline_counts) and today_count:
        claims.add("new_signal")
    if any(baseline_counts) and today_count >= 3 and today_count >= baseline_mean + 2 and today_count >= baseline_mean * 1.5:
        claims.add("rising")
    return sorted(claims)


def recall_report_candidates(features):
    candidates = []
    for row in features:
        reasons = _recall_reasons(row)
        exclusion = "feature_policy_excluded" if row["feature_policy"]["action"] == "exclude" else None
        if reasons or exclusion:
            candidates.append({
                **row,
                "candidate_id": "candidate:" + row["daily_topic_id"],
                "recall_reasons": sorted(reasons),
                "candidate_band": _candidate_band(reasons, row),
                "allowed_trend_claims": classify_allowed_trend_claims(
                    row["today_conversation_count"], row["baseline_daily_counts"], row["historical_active_dates"]
                ),
                "deterministic_exclusion": exclusion,
            })
    return sorted(candidates, key=lambda row: row["candidate_id"])
```

`_recall_reasons` must keep the five routes independent. `high_value_single` is broad recall only: require one conversation, at least one evidence span, a nonempty source link, and either known platform/version/feature context or a concrete Bug/request feedback type. The model remains responsible for deciding whether the single feedback is actually valuable.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_report_signals.py -q`

Expected: PASS.

- [ ] **Step 5: Commit recall and trend rules**

```bash
git add feedback_hub/topic_discovery/report_signals.py feedback_hub/tests/test_topic_report_signals.py
git commit -m "feat: recall auditable daily report signals"
```

---

### Task 3: Build report-layer candidate relations without changing topic memory

**Files:**
- Create: `feedback_hub/topic_discovery/insight_editor.py`
- Test: `feedback_hub/tests/test_topic_insight_editor.py`

**Interfaces:**
- Consumes: recalled candidates, report-day topic embeddings, and lifecycle parent/stable-topic relationships.
- Produces: `build_candidate_relation_plan(candidates, daily_topic_ids, daily_embeddings, *, threshold=0.78, top_k=5, max_bucket_size=8) -> dict[str, Any]`.
- Output has `relations`, bounded `buckets`, `singleton_candidate_ids`, and deterministic stats.

- [ ] **Step 1: Write failing relation tests**

```python
def test_relation_plan_prioritizes_same_stable_topic_and_parent_links():
    candidates = [
        _candidate("c1", "d1", stable="t1"),
        _candidate("c2", "d2", stable="t1"),
        _candidate("c3", "d3", stable="t2", parent_ids=["t1"]),
    ]
    vectors = np.asarray([[1.0, 0.0], [0.99, 0.01], [0.8, 0.6]], dtype="float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    plan = build_candidate_relation_plan(candidates, ["d1", "d2", "d3"], vectors)

    relation_types = {row["relation_type"] for row in plan["relations"]}
    assert "same_stable_topic" in relation_types
    assert "parent_subtopic" in relation_types
    assert {item["candidate_id"] for item in plan["buckets"][0]["items"]} == {"c1", "c2", "c3"}


def test_relation_plan_never_drops_or_duplicates_candidates():
    plan = build_candidate_relation_plan(
        [_candidate("c1", "d1"), _candidate("c2", "d2")],
        ["d1", "d2"],
        np.eye(2, dtype="float32"),
    )
    ids = [item["candidate_id"] for bucket in plan["buckets"] for item in bucket["items"]]
    ids += plan["singleton_candidate_ids"]
    assert sorted(ids) == ["c1", "c2"]
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_editor.py -q`

Expected: FAIL because `insight_editor.py` does not exist.

- [ ] **Step 3: Implement bounded relation components**

```python
def build_candidate_relation_plan(candidates, daily_topic_ids, daily_embeddings, *, threshold=0.78, top_k=5, max_bucket_size=8):
    _validate_candidate_embeddings(candidates, daily_topic_ids, daily_embeddings)
    vectors = _candidate_vectors(candidates, daily_topic_ids, daily_embeddings)
    semantic_edges = _top_k_edges(candidates, vectors, threshold=threshold, top_k=top_k)
    structural_edges = _structural_edges(candidates)
    relations = _dedupe_relations([*structural_edges, *semantic_edges])
    buckets, singleton_ids = _bounded_components(candidates, relations, max_bucket_size=max_bucket_size)
    return {
        "relations": relations,
        "buckets": buckets,
        "singleton_candidate_ids": singleton_ids,
        "stats": {
            "candidates": len(candidates),
            "relations": len(relations),
            "buckets": len(buckets),
            "singletons": len(singleton_ids),
        },
    }
```

Structural edges sort ahead of semantic edges. Component construction must remain deterministic and bounded. It records suggestions only and must not import or call `apply_topic_events`.

- [ ] **Step 4: Run relation tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_editor.py -q`

Expected: PASS.

- [ ] **Step 5: Commit report-layer relation planning**

```bash
git add feedback_hub/topic_discovery/insight_editor.py feedback_hub/tests/test_topic_insight_editor.py
git commit -m "feat: relate atomic topics for report editing"
```

---

### Task 4: Add strict topic-level editor prompts, parsing, and multi-route execution

**Files:**
- Modify: `feedback_hub/topic_discovery/insight_editor.py`
- Modify: `feedback_hub/tests/test_topic_insight_editor.py`

**Interfaces:**
- Produces: `build_insight_editor_prompt(bucket) -> str`.
- Produces: `parse_insight_editor_reply(reply, *, allowed_candidate_ids, allowed_trend_claims) -> list[dict[str, Any]]`.
- Produces: `run_insight_editor_multi_channel(buckets, *, routes, output_path, concurrency_per_route=4, resume=False, call_model=call_model_route) -> tuple[list[dict], dict]`.
- Parser requires exact candidate coverage across insight outputs and validates all IDs, decisions, types, trend claims, confidence, and source links.

- [ ] **Step 1: Write failing prompt and parser tests**

```python
def test_editor_prompt_separates_atomic_topics_from_report_insights():
    prompt = build_insight_editor_prompt(_bucket([_candidate("c1", "d1")]))
    assert "Report grouping must not modify topic memory" in prompt
    assert "new_topic does not prove a product-level new issue" in prompt
    assert "Do not merge merely because candidates share a feature" in prompt
    assert "main | observe | exclude | manual_review" in prompt


def test_parser_rejects_unsupported_rising_claim_and_requires_exact_coverage():
    reply = json.dumps({"insights": [{
        "report_decision": "main",
        "signal_type": "rising_or_repeated_bug",
        "headline": "语音不上屏增加",
        "summary": "当天出现多次",
        "selection_reason": "重复反馈",
        "trend_claim": "rising",
        "source_candidate_ids": ["c1"],
        "representative_issue_unit_ids": ["i1"],
        "confidence": 0.9,
        "needs_human_review": False,
    }]}, ensure_ascii=False)
    with pytest.raises(ValueError, match="unsupported trend claim"):
        parse_insight_editor_reply(
            reply,
            allowed_candidate_ids={"c1"},
            allowed_trend_claims={"c1": {"none", "repeated"}},
        )
```

- [ ] **Step 2: Run editor tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_editor.py -q`

Expected: FAIL because prompt, parser, and runner functions do not exist.

- [ ] **Step 3: Implement prompt and strict parser**

```python
_REPORT_DECISIONS = {"main", "observe", "exclude", "manual_review"}
_SIGNAL_TYPES = {"new_bug", "rising_or_repeated_bug", "demand_opportunity", "high_value_single", "not_reportable"}


def parse_insight_editor_reply(reply, *, allowed_candidate_ids, allowed_trend_claims):
    payload = json.loads(_extract_json(reply))
    insights = payload.get("insights")
    if not isinstance(insights, list):
        raise ValueError("insights must be a list")
    rows = [_normalize_insight(value) for value in insights]
    covered = [candidate_id for row in rows for candidate_id in row["source_candidate_ids"]]
    if len(covered) != len(set(covered)) or set(covered) != allowed_candidate_ids:
        raise ValueError("insight candidate coverage must be exact")
    for row in rows:
        legal = set.intersection(*(set(allowed_trend_claims[candidate_id]) for candidate_id in row["source_candidate_ids"]))
        if row["trend_claim"] not in legal:
            raise ValueError("unsupported trend claim")
    return rows
```

The parser must also reject empty headlines for `main`/`observe`, unknown issue-unit IDs, unsupported enum values, duplicate candidate IDs, and `main` rows without source links in their candidate evidence. It must never downgrade an invalid `rising` claim silently.

- [ ] **Step 4: Implement the scheduler wrapper using existing route infrastructure**

```python
def run_insight_editor_multi_channel(buckets, *, routes, output_path, concurrency_per_route=4, resume=False, call_model=call_model_route):
    normalized_routes = normalize_model_routes(routes)
    jobs = [(index, bucket["bucket_id"], bucket) for index, bucket in enumerate(buckets)]

    def worker(index, key, bucket, route):
        prompt = build_insight_editor_prompt(bucket)
        reply = invoke_model_route(prompt, route=route, call_model=call_model)
        decisions = parse_insight_editor_reply(
            reply.content,
            allowed_candidate_ids={item["candidate_id"] for item in bucket["items"]},
            allowed_trend_claims={item["candidate_id"]: set(item["allowed_trend_claims"]) for item in bucket["items"]},
        )
        return ({
            "batch_key": key,
            "insights": decisions,
            "route_source": reply.route_name,
            "endpoint_class": reply.endpoint_class,
            "model": reply.model,
            "attempts": reply.attempts,
            "elapsed_ms": reply.elapsed_ms,
            "parse_status": "ok",
        }, None)

    return run_pauseable_model_jobs(
        jobs, worker, routes=normalized_routes, output_path=output_path,
        concurrency_per_route=concurrency_per_route, resume=resume,
    )
```

- [ ] **Step 5: Test route balancing, parse failures, resume, and credential redaction**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_editor.py feedback_hub/tests/test_topic_model_routes.py -q`

Expected: PASS.

- [ ] **Step 6: Commit topic-level editor execution**

```bash
git add feedback_hub/topic_discovery/insight_editor.py feedback_hub/tests/test_topic_insight_editor.py
git commit -m "feat: judge daily insights with topic-level models"
```

---

### Task 5: Select at most five main insights and render auditable artifacts

**Files:**
- Create: `feedback_hub/topic_discovery/insight_artifacts.py`
- Test: `feedback_hub/tests/test_topic_insight_artifacts.py`

**Interfaces:**
- Produces: `select_main_insights(insights, candidates, *, max_items=5) -> dict[str, list[dict]]` with `main`, `observe`, `manual_review`, and `exclude`.
- Produces: `render_daily_report(report_date, selected, media_appendix, run_summary) -> str`.
- Produces: `write_review_workbook(path, *, selected, candidates, relations, media_appendix) -> None`.

- [ ] **Step 1: Write failing selection and rendering tests**

```python
def test_select_main_insights_caps_without_filling_or_duplication():
    insights = [_insight(f"i{n}", "main", confidence=0.9 - n / 100) for n in range(7)]
    selected = select_main_insights(insights, _candidate_map(insights), max_items=5)
    assert len(selected["main"]) == 5
    assert len(selected["observe"]) == 2
    assert len({row["insight_id"] for row in selected["main"]}) == 5


def test_main_insight_without_link_is_demoted_to_manual_review():
    selected = select_main_insights([_insight("i1", "main")], {"c1": _candidate("c1", links=[])})
    assert selected["main"] == []
    assert selected["manual_review"][0]["artifact_reason"] == "missing_source_link"


def test_report_contains_clickable_sources_and_separate_media_appendix():
    text = render_daily_report("2026-07-14", _selected(), [_media("https://feedback/media")], _summary())
    assert "[查看原反馈](https://feedback/main)" in text
    assert "媒体附录" in text
    assert "https://feedback/media" in text
```

- [ ] **Step 2: Run artifact tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_artifacts.py -q`

Expected: FAIL because `insight_artifacts.py` does not exist.

- [ ] **Step 3: Implement deterministic selection and Markdown rendering**

```python
_TYPE_ORDER = {
    "rising_or_repeated_bug": 0,
    "new_bug": 1,
    "demand_opportunity": 2,
    "high_value_single": 3,
    "not_reportable": 4,
}


def select_main_insights(insights, candidates, *, max_items=5):
    normalized = [_attach_candidate_evidence(row, candidates) for row in insights]
    for row in normalized:
        if row["report_decision"] == "main" and not row["source_links"]:
            row["report_decision"] = "manual_review"
            row["artifact_reason"] = "missing_source_link"
    main = sorted(
        (row for row in normalized if row["report_decision"] == "main"),
        key=lambda row: (_TYPE_ORDER[row["signal_type"]], -row["confidence"], row["insight_id"]),
    )
    overflow = main[max_items:]
    for row in overflow:
        row["report_decision"] = "observe"
        row["artifact_reason"] = "main_limit_overflow"
    return _partition([*main[:max_items], *overflow, *(row for row in normalized if row not in main)])
```

The Markdown renderer must state the report and baseline dates, use unique-conversation counts, include evidence limitations, and never include route tokens or raw prompts.

- [ ] **Step 4: Implement the four-sheet review workbook with openpyxl hyperlinks**

```python
def _write_link(cell, url, label="查看原反馈"):
    cell.value = label
    cell.hyperlink = url
    cell.style = "Hyperlink"


def write_review_workbook(path, *, selected, candidates, relations, media_appendix):
    workbook = Workbook()
    _write_main_sheet(workbook.active, selected)
    _write_candidate_sheet(workbook.create_sheet("全部候选"), candidates)
    _write_relation_sheet(workbook.create_sheet("原子主题关系"), relations)
    _write_media_sheet(workbook.create_sheet("媒体附录"), media_appendix)
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    workbook.save(path)
```

`正文与观察` must include `human_report_decision`, `human_signal_type`, `human_grouping_result`, and `human_notes`. Workbook tests must reload the file with `openpyxl.load_workbook` and assert sheet names, hyperlink targets, and human-review columns.

- [ ] **Step 5: Run artifact tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_artifacts.py -q`

Expected: PASS.

- [ ] **Step 6: Commit report selection and artifact rendering**

```bash
git add feedback_hub/topic_discovery/insight_artifacts.py feedback_hub/tests/test_topic_insight_artifacts.py
git commit -m "feat: render daily insight review artifacts"
```

---

### Task 6: Add a resumable end-to-end experiment runner

**Files:**
- Create: `feedback_hub/topic_discovery/daily_insight_run.py`
- Create: `scripts/run_daily_insight_signal.py`
- Create: `feedback_hub/tests/test_daily_insight_run.py`

**Interfaces:**
- Produces: `DailyInsightConfig` with `run_root`, `report_date`, `baseline_days`, `catalog_path`, `output_dir`, routes, concurrency, relation thresholds, and resume.
- Produces: `run_daily_insight_experiment(config) -> dict[str, Any]`.
- CLI loads credentials only from named environment variables and prints a credential-free summary.

- [ ] **Step 1: Write failing orchestration tests with a fake editor route**

```python
def test_run_daily_insight_experiment_writes_complete_artifact_set(tmp_path):
    run_root = _write_shadow_fixture(tmp_path, report_topics=2)
    config = DailyInsightConfig(
        run_root=run_root,
        report_date=date(2026, 7, 14),
        baseline_days=7,
        catalog_path=_write_catalog(tmp_path),
        output_dir=tmp_path / "out",
        routes=(_fake_route(),),
        concurrency_per_route=1,
    )
    summary = run_daily_insight_experiment(config, call_model=_fake_editor_call)
    assert summary["run_status"] == "completed"
    assert summary["feature_topics"] == 2
    assert (config.output_dir / "topic_signal_features.jsonl").exists()
    assert (config.output_dir / "daily_insight_review.xlsx").exists()
    assert (config.output_dir / "daily_report_draft_20260714.md").exists()


def test_run_stops_before_report_when_editor_has_unresolved_failure(tmp_path):
    config = _config(tmp_path)
    summary = run_daily_insight_experiment(config, call_model=_failing_call)
    assert summary["run_status"] == "incomplete_model_failures"
    assert not (config.output_dir / "daily_report_draft_20260714.md").exists()
```

- [ ] **Step 2: Run orchestration tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_daily_insight_run.py -q`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement staged orchestration with exact reconciliation**

```python
def run_daily_insight_experiment(config, *, call_model=call_model_route):
    days = load_shadow_days(config.run_root, config.report_date, config.baseline_days)
    catalog = FeatureCatalog.from_xlsx(config.catalog_path)
    features = build_topic_signal_features(days, days[-1]["topic_store"], catalog, report_date=config.report_date.isoformat())
    _write_jsonl(config.output_dir / "topic_signal_features.jsonl", features)
    candidates = recall_report_candidates(features)
    _write_jsonl(config.output_dir / "candidate_recall.jsonl", candidates)
    relation_plan = build_candidate_relation_plan(
        [row for row in candidates if not row["deterministic_exclusion"]],
        days[-1]["daily_topic_ids"], days[-1]["daily_topic_embeddings"],
        threshold=config.relation_threshold, top_k=config.relation_top_k,
    )
    _write_jsonl(config.output_dir / "candidate_relations.jsonl", relation_plan["relations"])
    buckets = _editor_buckets(candidates, relation_plan)
    model_rows, model_stats = run_insight_editor_multi_channel(
        buckets, routes=list(config.routes), output_path=config.output_dir / "insight_model_rows.jsonl",
        concurrency_per_route=config.concurrency_per_route, resume=config.resume, call_model=call_model,
    )
    if model_stats["run_status"] != "completed" or model_stats["failed"]:
        return _write_incomplete_summary(config, features, candidates, model_stats)
    insights = _flatten_and_validate_insights(model_rows, candidates)
    _write_jsonl(config.output_dir / "insight_decisions.jsonl", insights)
    selected = select_main_insights(insights, _candidate_map(candidates), max_items=5)
    return _write_completed_artifacts(config, selected, candidates, relation_plan, days[-1]["media_appendix"], model_stats)
```

The loader must verify all eight expected day directories, the report date, the 404-topic report-day count from source data rather than a hardcoded constant, and matching issue embedding rows. It must not load old report-candidate outputs.

- [ ] **Step 4: Add CLI arguments and environment-only credentials**

```python
parser.add_argument("--run-root", required=True)
parser.add_argument("--report-date", required=True)
parser.add_argument("--catalog", required=True)
parser.add_argument("--output-dir", required=True)
parser.add_argument("--baseline-days", type=int, default=7)
parser.add_argument("--route-a-url", default="")
parser.add_argument("--route-a-token-env", default="KNOT_API_TOKEN_A")
parser.add_argument("--route-c-url", default="")
parser.add_argument("--route-c-token-env", default="KNOT_API_TOKEN_C")
parser.add_argument("--openai-url", default="")
parser.add_argument("--openai-key-env", default="GLM_API_KEY")
parser.add_argument("--openai-model", default="")
parser.add_argument("--concurrency-per-route", type=int, default=4)
parser.add_argument("--resume", action="store_true")
```

Use the existing `ModelRoute` normalization. Do not embed default tokens or print environment values.

- [ ] **Step 5: Run orchestration and existing regression tests**

Run: `python3 -m pytest feedback_hub/tests/test_daily_insight_run.py feedback_hub/tests/test_topic_report_signals.py feedback_hub/tests/test_topic_insight_editor.py feedback_hub/tests/test_topic_insight_artifacts.py feedback_hub/tests/test_topic_shadow_run.py feedback_hub/tests/test_topic_model_routes.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the end-to-end runner**

```bash
git add feedback_hub/topic_discovery/daily_insight_run.py scripts/run_daily_insight_signal.py feedback_hub/tests/test_daily_insight_run.py
git commit -m "feat: run resumable daily insight experiments"
```

---

### Task 7: Run and verify the 2026-07-14 experiment

**Files:**
- Create under ignored output data: `outputs/daily_insight_signal_20260714/topic_signal_features.jsonl`
- Create under ignored output data: `outputs/daily_insight_signal_20260714/candidate_recall.jsonl`
- Create under ignored output data: `outputs/daily_insight_signal_20260714/candidate_relations.jsonl`
- Create under ignored output data: `outputs/daily_insight_signal_20260714/insight_decisions.jsonl`
- Create under ignored output data: `outputs/daily_insight_signal_20260714/daily_insight_review.xlsx`
- Create under ignored output data: `outputs/daily_insight_signal_20260714/daily_report_draft_20260714.md`
- Create under ignored output data: `outputs/daily_insight_signal_20260714/run_summary.json`

**Interfaces:**
- Consumes: `feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run/` and `feedback_hub/data/label_v2_samples/feature_knowledge_template_20260701.xlsx`.
- Produces: the user-reviewable workbook and report draft defined in the design.

- [ ] **Step 1: Run all targeted tests before real model calls**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_report_signals.py \
  feedback_hub/tests/test_topic_insight_editor.py \
  feedback_hub/tests/test_topic_insight_artifacts.py \
  feedback_hub/tests/test_daily_insight_run.py \
  feedback_hub/tests/test_topic_shadow_run.py \
  feedback_hub/tests/test_topic_model_routes.py -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run the real experiment with available routes**

Run:

```bash
python3 scripts/run_daily_insight_signal.py \
  --run-root feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run \
  --report-date 2026-07-14 \
  --baseline-days 7 \
  --catalog feedback_hub/data/label_v2_samples/feature_knowledge_template_20260701.xlsx \
  --output-dir outputs/daily_insight_signal_20260714 \
  --openai-url https://othersapi.com/v1/chat/completions \
  --openai-key-env GLM_API_KEY \
  --openai-model glm-5.2 \
  --concurrency-per-route 4
```

Expected: exit code 0 and JSON stdout with `run_status=completed`. If a configured route reaches explicit quota, preserve checkpoints and rerun with `--resume` after route availability is resolved.

- [ ] **Step 3: Verify machine-readable reconciliation and report limits**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path("outputs/daily_insight_signal_20260714")
summary = json.loads((root / "run_summary.json").read_text())
features = [json.loads(line) for line in (root / "topic_signal_features.jsonl").read_text().splitlines() if line]
decisions = [json.loads(line) for line in (root / "insight_decisions.jsonl").read_text().splitlines() if line]
assert summary["run_status"] == "completed"
assert len(features) == 404
assert len({row["daily_topic_id"] for row in features}) == 404
assert summary["main_insights"] <= 5
assert all(row.get("source_candidate_ids") for row in decisions)
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
```

Expected: assertions pass and the summary prints without credentials.

- [ ] **Step 4: Verify workbook integrity and clickable links**

Run:

```bash
python3 - <<'PY'
from openpyxl import load_workbook

path = "outputs/daily_insight_signal_20260714/daily_insight_review.xlsx"
book = load_workbook(path, read_only=False, data_only=False)
assert book.sheetnames == ["正文与观察", "全部候选", "原子主题关系", "媒体附录"]
headers = [cell.value for cell in book["正文与观察"][1]]
for name in ("human_report_decision", "human_signal_type", "human_grouping_result", "human_notes"):
    assert name in headers
assert any(cell.hyperlink and cell.hyperlink.target.startswith("http") for sheet in book.worksheets for row in sheet.iter_rows() for cell in row)
print(book.sheetnames)
PY
```

Expected: all four sheets print and at least one HTTP hyperlink exists.

- [ ] **Step 5: Scan outputs and tracked code for credentials**

Run:

```bash
rg -n 'sk-[A-Za-z0-9_-]{16,}|token[=:][[:space:]]*[A-Za-z0-9_-]{24,}|api[_-]?key[=:][[:space:]]*[A-Za-z0-9_-]{16,}' \
  feedback_hub/topic_discovery scripts outputs/daily_insight_signal_20260714
```

Expected: no output and exit code 1 from `rg` because no credentials are present.

- [ ] **Step 6: Perform Codex review before user handoff**

Review every `main` insight, every `manual_review` insight, all report-layer groups containing more than one atomic topic, and all `rising` claims. Fill Codex recommendation columns for clear cases; leave genuinely ambiguous human fields blank. Confirm that the Markdown report has at most five main items, each with a working source link and no unsupported product conclusion.

- [ ] **Step 7: Deliver the two primary review files**

Provide clickable local links to:

- `outputs/daily_insight_signal_20260714/daily_insight_review.xlsx`
- `outputs/daily_insight_signal_20260714/daily_report_draft_20260714.md`

Summarize candidate counts, main/observe/manual-review/exclude counts, model route share, parse failures, and any ambiguous rows requiring user judgment.

---

## Plan Self-Review

- **Spec coverage:** Tasks 1-2 cover deterministic statistics, policy semantics, broad recall, and trend evidence. Tasks 3-4 cover report-only grouping and topic-level LLM judgment. Task 5 covers the five-item cap, media appendix, source links, Markdown, and workbook. Tasks 6-7 cover resumability, route provenance, failures, exact 404-topic reconciliation, credentials, and the real 2026-07-14 review handoff.
- **Isolation:** No task writes the stable topic store or lifecycle events. The experiment consumes completed shadow artifacts read-only.
- **Type consistency:** `candidate_id`, `daily_topic_id`, `stable_topic_id`, `source_candidate_ids`, `allowed_trend_claims`, and `report_decision` use the same names across feature, editor, artifact, and runner interfaces.
- **Scope:** This plan does not implement weekly thresholds, production scheduling, media interpretation, fixed topic taxonomy, or upstream relabeling.
