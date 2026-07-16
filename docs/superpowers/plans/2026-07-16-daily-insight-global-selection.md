# Daily Insight Global Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace local-batch `main` truncation with an auditable shortlist and one global model selection that produces zero to five final daily-report insights.

**Architecture:** Keep deterministic feature extraction, broad recall, candidate relations, and local topic editing intact. Change the local editor decision to `nominate`, build a bounded four-lane shortlist from enriched local insights, run one strict global comparison call, and render the report only from its validated ranked selection. Resume must reuse the completed 2026-07-14 local model rows and migrate legacy `main` values without rerunning those calls.

**Tech Stack:** Python 3.9+, pytest, JSON/JSONL, openpyxl, existing `feedback_hub.topic_discovery.model_routes` scheduler, existing 2026-07-14 shadow artifacts.

## Global Constraints

- Counts remain deduplicated by `conversation_id`, never by message count.
- Local editor decisions are `nominate | observe | exclude | manual_review`; local `nominate` never directly means final report inclusion.
- Legacy persisted local `main` values are migrated to `nominate` at load time and remain traceable.
- Shortlist lane limits default to 10 repeated/trend bugs, 8 new bugs, 8 demand opportunities, and 4 high-value singles; the deduplicated total never exceeds 30.
- There is no fixed final-report quota by signal type and no minimum report size.
- A strong local `observe` can outrank a weaker `nominate`; local decision is only the final tie-breaker after evidence fields.
- `exclude`, `manual_review`, `needs_human_review`, and linkless insights cannot enter the shortlist.
- The global model can select zero to five supplied insight IDs and cannot change their title, signal type, evidence, or trend claim.
- Final selected ranks must be unique and consecutive from 1.
- Selected insights cannot overlap on any `source_candidate_id`.
- Global selection failure produces `selection_blocked`; no deterministic fallback is allowed to populate report main items.
- Model credentials remain environment-only and never appear in source, tests, logs, or artifacts.
- Report grouping and selection do not modify `topic_store.jsonl` or lifecycle events.

---

### Task 1: Change local editor output from `main` to `nominate`

**Files:**
- Modify: `feedback_hub/topic_discovery/insight_editor.py`
- Modify: `feedback_hub/tests/test_topic_insight_editor.py`

**Interfaces:**
- `build_insight_editor_prompt(bucket: dict[str, Any]) -> str` advertises `nominate | observe | exclude | manual_review`.
- `parse_insight_editor_reply(reply: str, *, allowed_candidate_ids: set[str], allowed_trend_claims: dict[str, set[str]], allowed_issue_unit_ids: set[str] | None = None) -> list[dict[str, Any]]` accepts `nominate` and rejects legacy `main` in new replies.
- Local link validation applies to `nominate` because only linked insights can advance.

- [x] **Step 1: Write failing prompt, parser, and link-validation tests**

```python
def _reply(decision: str, *, candidate_id: str, trend_claim: str) -> str:
    return json.dumps({"insights": [{
        "report_decision": decision,
        "signal_type": "new_bug",
        "headline": "语音输入无文字",
        "summary": "用户说完后没有文字上屏",
        "selection_reason": "证据明确",
        "trend_claim": trend_claim,
        "source_candidate_ids": [candidate_id],
        "representative_issue_unit_ids": [f"i-{candidate_id}"],
        "confidence": 0.8,
        "needs_human_review": False,
    }]}, ensure_ascii=False)


def test_editor_prompt_uses_nominate_instead_of_final_main() -> None:
    prompt = build_insight_editor_prompt(_bucket([_candidate("c1", "d1")]))
    assert "nominate | observe | exclude | manual_review" in prompt
    assert "nominate does not mean final report inclusion" in prompt
    assert "main | observe" not in prompt


def test_parser_accepts_nominate_and_rejects_legacy_main() -> None:
    nominate = _reply("nominate", candidate_id="c1", trend_claim="none")
    rows = parse_insight_editor_reply(
        nominate,
        allowed_candidate_ids={"c1"},
        allowed_trend_claims={"c1": {"none"}},
        allowed_issue_unit_ids={"i-c1"},
    )
    assert rows[0]["report_decision"] == "nominate"

    with pytest.raises(ValueError, match="unsupported report decision"):
        parse_insight_editor_reply(
            _reply("main", candidate_id="c1", trend_claim="none"),
            allowed_candidate_ids={"c1"},
            allowed_trend_claims={"c1": {"none"}},
            allowed_issue_unit_ids={"i-c1"},
        )


def test_nominate_requires_a_source_link() -> None:
    with pytest.raises(ValueError, match="nominate insight requires a source link"):
        _validate_nominate_links(
            [{"report_decision": "nominate", "source_candidate_ids": ["c1"]}],
            [{"candidate_id": "c1", "source_links": []}],
        )
```

- [x] **Step 2: Run the focused tests and verify they fail**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_editor.py -q`

Expected: FAIL because the prompt and parser still use `main`.

- [x] **Step 3: Update the local schema and prompt semantics**

```python
_REPORT_DECISIONS = {"nominate", "observe", "exclude", "manual_review"}


def _validate_nominate_links(decisions, items):
    candidate_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in items
    }
    for insight in decisions:
        if insight["report_decision"] != "nominate":
            continue
        links = {
            str(link).strip()
            for candidate_id in insight["source_candidate_ids"]
            for link in candidate_by_id[candidate_id].get("source_links") or []
            if str(link).strip()
        }
        if not links:
            raise ValueError("nominate insight requires a source link")
```

Change prompt copy to state that `nominate` means “advance to global comparison” and not “final report inclusion”. Rename `_validate_main_links` and update its caller. Set `prompt_version` to `daily_insight_editor_v2_nominate`.

- [x] **Step 4: Run the focused tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_editor.py -q`

Expected: PASS.

- [x] **Step 5: Commit the local-decision migration**

```bash
git add feedback_hub/topic_discovery/insight_editor.py feedback_hub/tests/test_topic_insight_editor.py
git commit -m "refactor: make daily insight main decisions nominations"
```

---

### Task 2: Build the auditable global shortlist and strict final selector

**Files:**
- Create: `feedback_hub/topic_discovery/global_selection.py`
- Create: `feedback_hub/tests/test_topic_global_selection.py`

**Interfaces:**
- `enrich_insights_with_evidence(insights, candidates) -> list[dict[str, Any]]` attaches deduplicated current and baseline conversation evidence.
- `build_global_shortlist(insights, candidates, *, lane_limits=None, max_items=30) -> list[dict[str, Any]]` returns evidence-ranked shortlist rows.
- `build_global_selection_prompt(shortlist) -> str` produces one compact global comparison request.
- `parse_global_selection_reply(reply: str, shortlist: list[dict[str, Any]]) -> dict[str, Any]` validates zero to five ranked supplied IDs.
- `run_global_selection_multi_channel(shortlist, *, routes, output_path, resume=False, call_fn=call_model_route) -> tuple[dict, dict]` persists one resumable global job.

- [x] **Step 1: Write failing shortlist tests for lane limits and local-decision independence**

```python
def _insight(
    insight_id: str,
    decision: str,
    signal_type: str,
    *,
    today: int = 1,
    baseline: list[int] | None = None,
    needs_human_review: bool = False,
) -> dict:
    return {
        "insight_id": insight_id,
        "report_decision": decision,
        "signal_type": signal_type,
        "headline": f"洞察{insight_id}",
        "summary": "明确的用户反馈",
        "selection_reason": "局部判断",
        "trend_claim": "none",
        "source_candidate_ids": [insight_id],
        "representative_issue_unit_ids": [f"issue-{insight_id}"],
        "confidence": 0.8,
        "needs_human_review": needs_human_review,
        "_fixture_today": today,
        "_fixture_baseline": list(baseline or [0] * 7),
    }


def _candidates_for(insights: list[dict]) -> dict[str, dict]:
    dates = [f"2026-07-{day:02d}" for day in range(7, 14)]
    result = {}
    for insight in insights:
        candidate_id = insight["source_candidate_ids"][0]
        today = insight["_fixture_today"]
        baseline = insight["_fixture_baseline"]
        result[candidate_id] = {
            "candidate_id": candidate_id,
            "daily_topic_id": f"daily:{candidate_id}",
            "stable_topic_id": f"topic:{candidate_id}",
            "title": insight["headline"],
            "description": insight["summary"],
            "today_conversation_ids": [f"today-{candidate_id}-{i}" for i in range(today)],
            "baseline_dates": dates,
            "baseline_conversation_ids_by_date": {
                day: [f"history-{candidate_id}-{day}-{i}" for i in range(count)]
                for day, count in zip(dates, baseline)
            },
            "source_links": [f"https://feedback/{candidate_id}"],
            "representative_issue_units": [{
                "issue_unit_id": f"issue-{candidate_id}",
                "summary": insight["summary"],
                "evidence_spans": ["明确证据"],
            }],
            "known_context": True,
            "platform_counts": {"Android": today},
            "feature_candidate_counts": {"voice_input": today},
            "appversion_counts": {},
            "recall_reasons": ["high_value_single"],
        }
    return result


def _lane_fixture(*, repeated: int, new: int, demand: int, single: int) -> list[dict]:
    rows = []
    for signal_type, count in (
        ("rising_or_repeated_bug", repeated),
        ("new_bug", new),
        ("demand_opportunity", demand),
        ("high_value_single", single),
    ):
        for index in range(count):
            rows.append(_insight(
                f"{signal_type}-{index:02d}",
                "nominate",
                signal_type,
                today=count - index,
                baseline=[1] * 7,
            ))
    return rows


def test_shortlist_uses_evidence_before_local_decision() -> None:
    insights = [
        _insight("weak", "nominate", "demand_opportunity", today=1, baseline=[0] * 7),
        _insight("strong", "observe", "demand_opportunity", today=8, baseline=[2] * 7),
    ]
    rows = build_global_shortlist(
        insights,
        _candidates_for(insights),
        lane_limits={"demand_opportunity": 1},
    )
    assert [row["insight_id"] for row in rows] == ["strong"]
    assert rows[0]["shortlist_lanes"] == ["demand_opportunity"]


def test_shortlist_caps_each_lane_and_total_without_composite_score() -> None:
    insights = _lane_fixture(repeated=12, new=10, demand=10, single=6)
    rows = build_global_shortlist(insights, _candidates_for(insights))
    assert len(rows) == 30
    assert Counter(
        lane
        for row in rows
        for lane in row["shortlist_lanes"]
    ) == Counter({
        "rising_or_repeated_bug": 10,
        "new_bug": 8,
        "demand_opportunity": 8,
        "high_value_single": 4,
    })
    assert all("shortlist_rank_fields" in row for row in rows)


def test_shortlist_excludes_review_linkless_and_excluded_rows() -> None:
    insights = [
        _insight("review", "manual_review", "new_bug"),
        _insight("flagged", "nominate", "new_bug", needs_human_review=True),
        _insight("excluded", "exclude", "not_reportable"),
        _insight("linkless", "nominate", "new_bug"),
    ]
    candidates = _candidates_for(insights)
    candidates["linkless"]["source_links"] = []
    assert build_global_shortlist(insights, candidates) == []
```

- [x] **Step 2: Run shortlist tests and verify import failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_global_selection.py -q`

Expected: FAIL because `global_selection.py` does not exist.

- [x] **Step 3: Implement evidence enrichment and four independent lane sort keys**

```python
DEFAULT_LANE_LIMITS = {
    "rising_or_repeated_bug": 10,
    "new_bug": 8,
    "demand_opportunity": 8,
    "high_value_single": 4,
}


def build_global_shortlist(insights, candidates, *, lane_limits=None, max_items=30):
    limits = dict(DEFAULT_LANE_LIMITS if lane_limits is None else lane_limits)
    enriched = enrich_insights_with_evidence(insights, candidates)
    eligible = [
        row for row in enriched
        if row.get("report_decision") not in {"exclude", "manual_review"}
        and not row.get("needs_human_review")
        and row.get("source_links")
        and row.get("signal_type") in limits
    ]
    selected = []
    for lane, limit in limits.items():
        lane_rows = [row for row in eligible if row["signal_type"] == lane]
        for lane_rank, row in enumerate(sorted(lane_rows, key=_lane_sort_key(lane))[:limit], 1):
            selected.append({
                **row,
                "shortlist_lanes": [lane],
                "shortlist_lane_rank": lane_rank,
                "shortlist_rank_fields": _rank_fields(row),
            })
    rows = _dedupe_shortlist(selected)
    if len(rows) > max_items:
        raise ValueError("global shortlist exceeds max_items")
    return rows
```

Use lexicographic keys, not a weighted score:

- repeated/trend: current conversations, active baseline days, baseline total, confidence, then `nominate` tie-break;
- new bug: current conversations, evidence-span count, known context, source-link count, confidence, then tie-break;
- demand: current conversations, active baseline days, baseline total, confidence, then tie-break;
- high-value single: evidence-span count, known context, source-link count, confidence, then tie-break.

- [x] **Step 4: Write failing global prompt and parser tests**

```python
def _shortlist_row(insight_id: str, *, source_candidate_ids: list[str] | None = None) -> dict:
    return {
        "insight_id": insight_id,
        "report_decision": "nominate",
        "signal_type": "new_bug",
        "headline": f"洞察{insight_id}",
        "summary": "明确问题",
        "selection_reason": "局部提名",
        "trend_claim": "new_signal",
        "source_candidate_ids": list(source_candidate_ids or [f"candidate-{insight_id}"]),
        "source_links": [f"https://feedback/{insight_id}"],
        "needs_human_review": False,
        "today_conversation_count": 2,
        "baseline_daily_counts": [0] * 7,
        "baseline_active_days": 0,
        "shortlist_lanes": ["new_bug"],
        "shortlist_rank_fields": {"today_conversation_count": 2},
    }


def _selection_reply(ids: list[str], *, ranks: list[int] | None = None) -> str:
    selected = []
    for index, insight_id in enumerate(ids):
        selected.append({
            "insight_id": insight_id,
            "rank": ranks[index] if ranks is not None else index + 1,
            "selection_reason": "相对证据更强",
            "editorial_note": "保持谨慎表达",
        })
    return json.dumps({
        "selected_insights": selected,
        "selection_summary": "完成全局比较",
    }, ensure_ascii=False)


def test_global_prompt_requests_relative_selection_without_new_facts() -> None:
    prompt = build_global_selection_prompt([_shortlist_row("i1")])
    assert "select zero to five" in prompt
    assert "compare every shortlist item" in prompt
    assert "must not change headline, signal_type, or trend_claim" in prompt
    assert "fixed type quota" in prompt


def test_global_parser_accepts_ranked_subset_and_empty_day() -> None:
    shortlist = [_shortlist_row("i1"), _shortlist_row("i2")]
    selected = parse_global_selection_reply(json.dumps({
        "selected_insights": [{
            "insight_id": "i2",
            "rank": 1,
            "selection_reason": "相对其他候选有更明确的当天增量",
            "editorial_note": "不要扩大影响范围",
        }],
        "selection_summary": "今日仅一条值得主动推送",
    }, ensure_ascii=False), shortlist)
    assert selected["selected_insights"][0]["insight_id"] == "i2"
    assert parse_global_selection_reply(
        '{"selected_insights": [], "selection_summary": "今日不推送"}',
        shortlist,
    )["selected_insights"] == []


@pytest.mark.parametrize("reply,error", [
    (_selection_reply(["unknown"]), "unknown insight"),
    (_selection_reply(["i1", "i2"], ranks=[1, 3]), "consecutive"),
    (_selection_reply(["i1"] * 2), "duplicate"),
])
def test_global_parser_rejects_invalid_selection(reply, error) -> None:
    with pytest.raises(ValueError, match=error):
        parse_global_selection_reply(reply, [_shortlist_row("i1"), _shortlist_row("i2")])


def test_global_parser_rejects_overlapping_source_candidates() -> None:
    shortlist = [
        _shortlist_row("i1", source_candidate_ids=["c1"]),
        _shortlist_row("i2", source_candidate_ids=["c1", "c2"]),
    ]
    with pytest.raises(ValueError, match="overlap"):
        parse_global_selection_reply(_selection_reply(["i1", "i2"]), shortlist)
```

- [x] **Step 5: Implement strict prompt, parser, and one-job resumable runner**

```python
def parse_global_selection_reply(reply, shortlist):
    payload = _load_json_object(reply)
    values = payload.get("selected_insights")
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("selected_insights must contain zero to five items")
    allowed = {row["insight_id"]: row for row in shortlist}
    ids = [str(row.get("insight_id") or "") for row in values]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate selected insight")
    if any(insight_id not in allowed for insight_id in ids):
        raise ValueError("unknown insight in global selection")
    ranks = [int(row.get("rank") or 0) for row in values]
    if ranks != list(range(1, len(values) + 1)):
        raise ValueError("global selection ranks must be consecutive")
    _validate_no_source_overlap(ids, allowed)
    return {
        "selected_insights": [_normalize_selection_row(row) for row in values],
        "selection_summary": str(payload.get("selection_summary") or "").strip(),
    }
```

Use `run_pauseable_model_jobs` with one stable job key, two parse attempts, route provenance, `prompt_version="daily_insight_global_selection_v1"`, and explicit `call_error`/`parse_error`. Never synthesize an empty successful selection from a failed call.

- [x] **Step 6: Run the new module tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_global_selection.py -q`

Expected: PASS.

- [x] **Step 7: Commit shortlist and selector logic**

```bash
git add feedback_hub/topic_discovery/global_selection.py feedback_hub/tests/test_topic_global_selection.py
git commit -m "feat: globally select daily report insights"
```

---

### Task 3: Render artifacts only from validated global selection

**Files:**
- Modify: `feedback_hub/topic_discovery/insight_artifacts.py`
- Modify: `feedback_hub/tests/test_topic_insight_artifacts.py`

**Interfaces:**
- Replace `select_main_insights(insights, candidates, *, max_items=5)` with `partition_selected_insights(insights, candidates, global_selection, *, shortlist) -> dict[str, list[dict[str, Any]]]`.
- `main` order comes only from global `rank`.
- Nonselected `nominate` and `observe` rows remain in `observe`; manual and excluded rows remain separate.
- Workbook rows include local decision, shortlist membership, global rank, global reason, and editorial note.

- [x] **Step 1: Replace truncation tests with global-selection tests**

```python
def test_partition_uses_only_ranked_global_selection_for_main() -> None:
    insights = [
        _insight("i1", "nominate", candidate_id="c1"),
        _insight("i2", "observe", candidate_id="c2"),
        _insight("i3", "nominate", candidate_id="c3"),
    ]
    selection = {
        "selected_insights": [{
            "insight_id": "i2",
            "rank": 1,
            "selection_reason": "当天增量最明确",
            "editorial_note": "保持谨慎表达",
        }],
        "selection_summary": "选择一条",
    }
    selected = partition_selected_insights(
        insights,
        {f"c{i}": _candidate(f"c{i}") for i in range(1, 4)},
        selection,
        shortlist=_shortlist("i1", "i2"),
    )
    assert [row["insight_id"] for row in selected["main"]] == ["i2"]
    assert {row["insight_id"] for row in selected["observe"]} == {"i1", "i3"}
    assert selected["main"][0]["global_selection_reason"] == "当天增量最明确"


def test_partition_does_not_fill_unused_slots() -> None:
    selected = partition_selected_insights(
        [_insight("i1", "nominate", candidate_id="c1")],
        {"c1": _candidate("c1")},
        {"selected_insights": [], "selection_summary": "今日不推送"},
        shortlist=_shortlist("i1"),
    )
    assert selected["main"] == []
    assert selected["observe"][0]["artifact_reason"] == "not_selected_globally"
```

- [x] **Step 2: Run artifact tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_artifacts.py -q`

Expected: FAIL because `partition_selected_insights` does not exist.

- [x] **Step 3: Implement selection-driven partitioning and remove `main_limit_overflow`**

```python
def partition_selected_insights(insights, candidates, global_selection, *, shortlist):
    normalized = enrich_insights_with_evidence(insights, candidates)
    by_id = {row["insight_id"]: row for row in normalized}
    shortlist_by_id = {row["insight_id"]: row for row in shortlist}
    selected_rows = global_selection.get("selected_insights") or []
    main = []
    selected_ids = set()
    for selection in selected_rows:
        insight_id = selection["insight_id"]
        row = dict(by_id[insight_id])
        row.update({
            "report_decision": "main",
            "global_rank": selection["rank"],
            "global_selection_reason": selection["selection_reason"],
            "editorial_note": selection["editorial_note"],
        })
        main.append(row)
        selected_ids.add(insight_id)
    observe = []
    for row in normalized:
        if row["insight_id"] in selected_ids or row["report_decision"] in {"exclude", "manual_review"}:
            continue
        copy = dict(row)
        copy["report_decision"] = "observe"
        copy["artifact_reason"] = (
            "not_selected_globally" if row["insight_id"] in shortlist_by_id
            else "not_shortlisted"
        )
        observe.append(copy)
    manual_review = sorted(
        (row for row in normalized if row["report_decision"] == "manual_review"),
        key=lambda row: row["insight_id"],
    )
    excluded = sorted(
        (row for row in normalized if row["report_decision"] == "exclude"),
        key=lambda row: row["insight_id"],
    )
    return {
        "main": sorted(main, key=lambda row: row["global_rank"]),
        "observe": sorted(observe, key=lambda row: row["insight_id"]),
        "manual_review": manual_review,
        "exclude": excluded,
    }
```

Delete the old type/confidence cap path. Keep deterministic evidence attachment in one implementation by importing `enrich_insights_with_evidence` from `global_selection.py`.

- [x] **Step 4: Update Markdown and XLSX assertions**

```python
def test_report_uses_global_reason_and_workbook_exposes_selection_audit(tmp_path) -> None:
    selected = {
        "main": [{
            **_insight("i1", "main", candidate_id="c1"),
            "global_rank": 1,
            "global_selection_reason": "相对于其他候选更值得关注",
            "editorial_note": "保持谨慎表达",
            "source_links": ["https://feedback/c1"],
            "today_conversation_count": 2,
            "baseline_daily_counts": [0] * 7,
        }],
        "observe": [],
        "manual_review": [],
        "exclude": [],
    }
    text = render_daily_report("2026-07-14", selected, [], {
        "baseline_start": "2026-07-07",
        "baseline_end": "2026-07-13",
        "route_counts": {"agent_a": 1},
    })
    assert "相对于其他候选更值得关注" in text

    path = tmp_path / "review.xlsx"
    write_review_workbook(path, selected=selected, candidates=[], relations=[], media_appendix=[])
    headers = [cell.value for cell in load_workbook(path)["正文与观察"][1]]
    assert "local_report_decision" in headers
    assert "global_rank" in headers
    assert "global_selection_reason" in headers
    assert "shortlist_lanes" in headers
```

The report “依据” line uses `global_selection_reason` for main items and local `selection_reason` for observations. Keep the workbook at four sheets and preserve all existing human-review columns and hyperlinks.

- [x] **Step 5: Run artifact tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_insight_artifacts.py -q`

Expected: PASS.

- [x] **Step 6: Commit global-selection artifact rendering**

```bash
git add feedback_hub/topic_discovery/insight_artifacts.py feedback_hub/tests/test_topic_insight_artifacts.py
git commit -m "feat: render globally selected daily insights"
```

---

### Task 4: Integrate global selection into the resumable daily runner

**Files:**
- Modify: `feedback_hub/topic_discovery/daily_insight_run.py`
- Modify: `feedback_hub/tests/test_daily_insight_run.py`
- Modify: `scripts/run_daily_insight_signal.py`

**Interfaces:**
- Resume reuses `insight_model_rows.jsonl`, normalizes legacy local decisions, and writes `global_shortlist.jsonl`.
- Global call writes `global_selection_model_rows.jsonl` plus `global_selection.json`.
- `run_summary.json` adds shortlist, selection, local/global route, and legacy migration counts.
- Report and workbook are generated only after successful global selection.

- [x] **Step 1: Write failing end-to-end tests for success, blocked selection, and legacy resume**

```python
def _fake_two_stage_call(prompt, **_kwargs):
    if "GLOBAL DAILY INSIGHT SELECTION" in prompt:
        items = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        return json.dumps({
            "selected_insights": [{
                "insight_id": items[0]["insight_id"],
                "rank": 1,
                "selection_reason": "相对比较后证据最明确",
                "editorial_note": "保持证据边界",
            }],
            "selection_summary": "本日选择一条",
        }, ensure_ascii=False)
    return _fake_local_nomination_call(prompt)


def test_run_writes_shortlist_and_global_selection_before_report(tmp_path) -> None:
    config = _config(tmp_path)
    summary = run_daily_insight_experiment(config, call_fn=_fake_two_stage_call)
    assert summary["run_status"] == "completed"
    assert summary["shortlist_insights"] >= 1
    assert summary["main_insights"] == 1
    assert (config.output_dir / "global_shortlist.jsonl").exists()
    assert (config.output_dir / "global_selection.json").exists()
    assert (config.output_dir / "daily_report_draft_20260714.md").exists()


def test_global_selection_failure_blocks_report_without_fallback(tmp_path) -> None:
    config = _config(tmp_path)
    def call(prompt, **kwargs):
        if "GLOBAL DAILY INSIGHT SELECTION" in prompt:
            return "not json"
        return _fake_local_nomination_call(prompt)
    summary = run_daily_insight_experiment(config, call_fn=call)
    assert summary["run_status"] == "selection_blocked"
    assert not (config.output_dir / "daily_report_draft_20260714.md").exists()
    assert (config.output_dir / "global_shortlist.jsonl").exists()


def test_legacy_main_rows_migrate_to_nominate_without_local_recall(tmp_path) -> None:
    rows = [{"insights": [{"insight_id": "i1", "report_decision": "main"}]}]
    normalized, count = _normalize_legacy_local_decisions(rows)
    assert normalized[0]["insights"][0]["report_decision"] == "nominate"
    assert normalized[0]["insights"][0]["legacy_report_decision"] == "main"
    assert count == 1
```

- [x] **Step 2: Run runner tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_daily_insight_run.py -q`

Expected: FAIL because no global stage or migration exists.

- [x] **Step 3: Integrate migration, shortlist, global call, and blocked status**

```python
model_rows, legacy_migrations = _normalize_legacy_local_decisions(model_rows)
insights = [insight for row in model_rows for insight in row.get("insights") or []]
insights.extend(_deterministic_excluded_insights(candidates))
_validate_insight_coverage(insights, candidates)
_write_jsonl(output_dir / "insight_decisions.jsonl", insights)

candidate_by_id = {str(row["candidate_id"]): row for row in candidates}
shortlist = build_global_shortlist(insights, candidate_by_id)
_write_jsonl(output_dir / "global_shortlist.jsonl", shortlist)
selection, selection_stats = run_global_selection_multi_channel(
    shortlist,
    routes=list(config.routes),
    output_path=output_dir / "global_selection_model_rows.jsonl",
    resume=config.resume,
    call_fn=call_fn,
)
if selection_stats["run_status"] != "completed":
    summary = _base_summary(
        config,
        features,
        candidates,
        relation_plan,
        model_stats,
        "selection_blocked",
    )
    summary.update({
        "shortlist_insights": len(shortlist),
        "legacy_main_migrations": legacy_migrations,
        "local_route_counts": dict(model_stats.get("route_counts") or {}),
        "global_route_counts": dict(selection_stats.get("route_counts") or {}),
        "global_selection_stats": dict(selection_stats),
    })
    _write_json(output_dir / "run_summary.json", summary)
    return summary
_write_json(output_dir / "global_selection.json", selection)
selected = partition_selected_insights(
    insights,
    candidate_by_id,
    selection,
    shortlist=shortlist,
)
```

When shortlist is empty, write an explicit successful empty `global_selection.json` without calling a model. On non-resume runs, remove stale report, workbook, shortlist, global selection, and global model-row files before work begins. Merge local and global route counts into a display total while preserving separate `local_route_counts` and `global_route_counts`.

- [x] **Step 4: Update CLI summary fields without adding credentials**

```python
print(json.dumps({
    "run_status": summary["run_status"],
    "report_date": summary["report_date"],
    "feature_topics": summary["feature_topics"],
    "candidate_topics": summary["candidate_topics"],
    "shortlist_insights": summary.get("shortlist_insights"),
    "main_insights": summary.get("main_insights"),
    "local_route_counts": summary.get("local_route_counts") or {},
    "global_route_counts": summary.get("global_route_counts") or {},
    "output_dir": str(config.output_dir),
}, ensure_ascii=False))
```

- [x] **Step 5: Run runner and CLI tests**

Run: `python3 -m pytest feedback_hub/tests/test_daily_insight_run.py -q`

Expected: PASS.

- [x] **Step 6: Run the complete focused regression suite**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_daily_insight_run.py \
  feedback_hub/tests/test_topic_report_signals.py \
  feedback_hub/tests/test_topic_insight_editor.py \
  feedback_hub/tests/test_topic_global_selection.py \
  feedback_hub/tests/test_topic_insight_artifacts.py \
  feedback_hub/tests/test_topic_shadow_run.py \
  feedback_hub/tests/test_topic_model_routes.py -q
```

Expected: PASS with no failed tests.

- [x] **Step 7: Commit runner integration**

```bash
git add \
  feedback_hub/topic_discovery/daily_insight_run.py \
  feedback_hub/tests/test_daily_insight_run.py \
  scripts/run_daily_insight_signal.py
git commit -m "feat: finalize daily insights with global selection"
```

---

### Task 5: Resume 2026-07-14, review the global shortlist, and verify artifacts

**Files:**
- Reuse: `feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run/`
- Reuse: `feedback_hub/data/label_v2_samples/feature_knowledge_template_20260701.xlsx`
- Update generated output: `outputs/daily_insight_signal_20260714/`

**Interfaces:**
- Existing 171 local model rows are resumed and migrated; only the global selection stage should make a new model request.
- Final artifacts include `global_shortlist.jsonl`, `global_selection.json`, revised Markdown, XLSX, and summary.

- [x] **Step 1: Resume the real run using environment-only credentials**

```bash
python3 scripts/run_daily_insight_signal.py \
  --run-root feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run \
  --report-date 2026-07-14 \
  --catalog feedback_hub/data/label_v2_samples/feature_knowledge_template_20260701.xlsx \
  --output-dir outputs/daily_insight_signal_20260714 \
  --route-a-url "$KNOT_AGENT_A_URL" \
  --route-c-url "$KNOT_AGENT_C_URL" \
  --openai-url "$GLM_OPENAI_URL" \
  --openai-model glm-5.2 \
  --resume
```

Expected: local scheduler reports all 171 rows resumed; global scheduler processes one request unless the shortlist is empty; `run_status` is `completed` or an explicit quota pause.

- [x] **Step 2: Verify coverage, limits, ranks, links, and legacy migration**

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path("outputs/daily_insight_signal_20260714")
load_jsonl = lambda name: [
    json.loads(line) for line in (root / name).read_text().splitlines() if line.strip()
]
candidates = load_jsonl("candidate_recall.jsonl")
decisions = load_jsonl("insight_decisions.jsonl")
shortlist = load_jsonl("global_shortlist.jsonl")
selection = json.loads((root / "global_selection.json").read_text())
selected = selection["selected_insights"]

assert len(shortlist) <= 30
assert len(selected) <= 5
assert [row["rank"] for row in selected] == list(range(1, len(selected) + 1))
assert {row["insight_id"] for row in selected} <= {row["insight_id"] for row in shortlist}
assert all(row["report_decision"] != "main" for row in decisions)
covered = [candidate_id for row in decisions for candidate_id in row["source_candidate_ids"]]
assert len(covered) == len(set(covered)) == len(candidates)
print({
    "candidates": len(candidates),
    "decisions": len(decisions),
    "shortlist": len(shortlist),
    "selected": len(selected),
})
PY
```

Expected: 248 candidates remain exactly covered, shortlist is at most 30, selection is zero to five, and no persisted local decision remains `main`.

- [x] **Step 3: Verify workbook structure and clickable feedback links**

```bash
python3 - <<'PY'
from openpyxl import load_workbook

path = "outputs/daily_insight_signal_20260714/daily_insight_review.xlsx"
book = load_workbook(path, data_only=False)
assert book.sheetnames == ["正文与观察", "全部候选", "原子主题关系", "媒体附录"]
headers = [cell.value for cell in book["正文与观察"][1]]
for name in ("local_report_decision", "global_rank", "global_selection_reason", "shortlist_lanes"):
    assert name in headers
links = [
    cell.hyperlink.target
    for sheet in book.worksheets
    for row in sheet.iter_rows()
    for cell in row
    if cell.hyperlink
]
assert links and all(link.startswith("http") for link in links)
print({"sheets": book.sheetnames, "links": len(links)})
PY
```

Expected: four review sheets and only valid HTTP(S) hyperlinks.

- [x] **Step 4: Review the final five-or-fewer items against the shortlist**

Record a concise review in `outputs/daily_insight_signal_20260714/codex_global_selection_review_20260716.md` covering:

- whether each selected item is supported by its source conversations;
- which strong shortlist items were not selected and whether that omission is acceptable;
- whether the report contains an unsupported new/rising claim;
- whether any two selected items overlap in product meaning;
- whether the final mix reflects actual relative value rather than a forced type quota.

- [x] **Step 5: Run secret and syntax verification**

```bash
python3 -m py_compile \
  feedback_hub/topic_discovery/global_selection.py \
  feedback_hub/topic_discovery/insight_editor.py \
  feedback_hub/topic_discovery/insight_artifacts.py \
  feedback_hub/topic_discovery/daily_insight_run.py \
  scripts/run_daily_insight_signal.py

rg -n --hidden --glob '!*.xlsx' --glob '!*.npz' \
  'sk-[A-Za-z0-9]{16,}|046ab8bd|7ecc8162|b2d9f00f' \
  feedback_hub/topic_discovery feedback_hub/tests scripts outputs/daily_insight_signal_20260714
```

Expected: compilation succeeds and the secret scan returns no matches.

- [x] **Step 6: Update task status and report the reviewed result**

Mark the global-selection implementation and 2026-07-14 result review complete only after all tests and artifact checks pass. Report the final selected count, shortlist count, route used for the global call, and any remaining manual-review caveats without exposing credentials.
