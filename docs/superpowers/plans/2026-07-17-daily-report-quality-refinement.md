# Daily Report Quality Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recompute report-layer trends after conversation deduplication, select both main and watch insights globally, and render a product-readable daily report that directly answers the four business questions.

**Architecture:** Keep the existing atomic-topic and local-editor outputs unchanged. Normalize trend and Bug type inside `enrich_insights_with_evidence`, extend the single global-selection call to return `selected_insights` plus `watch_insights`, and let the artifact layer render only those globally selected layers while retaining the full local pool for audit. Reuse the 2026-07-14 local checkpoints and make only one new global model call.

**Tech Stack:** Python 3, pytest, JSON/JSONL artifacts, openpyxl, existing multi-route LLM scheduler.

## Global Constraints

- Main report contains 0-5 globally selected insights and has no type quota.
- Watch section contains 0-5 globally selected watch insights and has no fallback fill.
- All report trends are deterministic results of aggregate deduplicated conversation counts.
- No image or video content is inferred.
- No unified composite importance score is introduced.
- Model credentials remain environment-only and never enter artifacts or commits.
- Existing unrelated worktree changes must not be staged, reverted, or reformatted.
- Production files are changed only after the corresponding test has failed for the expected reason.

---

## File Map

- `feedback_hub/topic_discovery/global_selection.py`: aggregate trend/type normalization, global main/watch prompt, parser, and validation.
- `feedback_hub/topic_discovery/insight_artifacts.py`: partition global main/watch layers, render the product report, and write review workbook rows.
- `feedback_hub/topic_discovery/daily_insight_run.py`: connect shortlist, global result, report renderer, and run summary.
- `scripts/run_daily_insight_signal.py`: expose watch counts in credential-free CLI output.
- `feedback_hub/tests/test_topic_global_selection.py`: trend normalization and global main/watch contract tests.
- `feedback_hub/tests/test_topic_insight_artifacts.py`: partition, report copy, evidence boundary, and workbook tests.
- `feedback_hub/tests/test_daily_insight_run.py`: end-to-end artifact integration test.
- `outputs/daily_insight_signal_20260714_v2/`: generated comparison artifacts; not committed as source code.

---

### Task 1: Normalize Aggregate Trend and Bug Type

**Files:**
- Modify: `feedback_hub/topic_discovery/global_selection.py:4-115`
- Test: `feedback_hub/tests/test_topic_global_selection.py`

**Interfaces:**
- Consumes: `classify_allowed_trend_claims(today_count: int, baseline_counts: list[int], active_dates: list[str]) -> list[str]`.
- Produces: enriched rows with `source_trend_claim`, `allowed_aggregate_trend_claims`, canonical `trend_claim`, `source_signal_type`, and normalized `signal_type`.

- [ ] **Step 1: Add failing aggregate trend tests**

Add tests that create grouped candidates with deduplicated baseline IDs rather than trusting their local trend:

```python
def test_enrichment_recomputes_rising_trend_and_bug_type_after_grouping() -> None:
    api = _api()
    insight = _insight("grouped", "nominate", "rising_or_repeated_bug", today=1)
    insight["trend_claim"] = "persistent"
    insight["source_candidate_ids"] = ["left", "right"]
    candidates = _candidates_for([
        _insight("left", "nominate", "rising_or_repeated_bug", today=3, baseline=[1, 3, 1, 3, 2, 3, 2]),
        _insight("right", "nominate", "rising_or_repeated_bug", today=2, baseline=[1, 3, 1, 3, 2, 3, 2]),
    ])
    for day in candidates["right"]["baseline_dates"]:
        candidates["right"]["baseline_conversation_ids_by_date"][day] = list(
            candidates["left"]["baseline_conversation_ids_by_date"][day]
        )

    row = api.enrich_insights_with_evidence([insight], candidates)[0]

    assert row["today_conversation_count"] == 5
    assert row["baseline_daily_counts"] == [1, 3, 1, 3, 2, 3, 2]
    assert row["source_trend_claim"] == "persistent"
    assert row["trend_claim"] == "rising"
    assert row["allowed_aggregate_trend_claims"] == ["none", "persistent", "repeated", "rising"]
    assert row["source_signal_type"] == "rising_or_repeated_bug"


def test_enrichment_turns_grouped_new_bug_into_repeated_bug_when_history_exists() -> None:
    insight = _insight("i1", "nominate", "new_bug", today=3, baseline=[1, 0, 1, 0, 0, 0, 0])
    insight["trend_claim"] = "new_signal"

    row = _api().enrich_insights_with_evidence([insight], _candidates_for([insight]))[0]

    assert row["source_signal_type"] == "new_bug"
    assert row["signal_type"] == "rising_or_repeated_bug"
    assert row["trend_claim"] == "reappeared"


@pytest.mark.parametrize(("baseline", "today", "expected"), [
    ([0, 0, 0, 0, 0, 0, 0], 1, "new_signal"),
    ([1, 0, 0, 0, 0, 0, 0], 3, "reappeared"),
    ([1, 1, 1, 1, 1, 1, 1], 4, "rising"),
    ([1, 0, 1, 0, 0, 1, 0], 1, "persistent"),
    ([0, 0, 0, 0, 0, 0, 0], 2, "new_signal"),
])
def test_canonical_trend_precedence(baseline: list[int], today: int, expected: str) -> None:
    assert _api().canonical_trend_claim(today, baseline)[0] == expected
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_global_selection.py::test_enrichment_recomputes_rising_trend_and_bug_type_after_grouping \
  feedback_hub/tests/test_topic_global_selection.py::test_enrichment_turns_grouped_new_bug_into_repeated_bug_when_history_exists \
  feedback_hub/tests/test_topic_global_selection.py::test_canonical_trend_precedence -q
```

Expected: FAIL because `canonical_trend_claim` and aggregate normalization fields do not exist.

- [ ] **Step 3: Implement canonical trend and type normalization**

Import the deterministic classifier and add a public helper:

```python
from feedback_hub.topic_discovery.report_signals import classify_allowed_trend_claims


_TREND_PRECEDENCE = (
    "new_signal", "reappeared", "rising", "persistent", "repeated", "none",
)


def canonical_trend_claim(today_count: int, baseline_counts: list[int]) -> tuple[str, list[str]]:
    allowed = classify_allowed_trend_claims(today_count, baseline_counts, [])
    allowed_set = set(allowed)
    claim = next(value for value in _TREND_PRECEDENCE if value in allowed_set)
    return claim, allowed


def _normalize_signal_type(signal_type: str, trend_claim: str) -> str:
    if signal_type == "new_bug" and trend_claim != "new_signal":
        return "rising_or_repeated_bug"
    if signal_type == "rising_or_repeated_bug" and trend_claim == "new_signal":
        return "new_bug"
    return signal_type
```

Inside `enrich_insights_with_evidence`, compute the canonical result after `baseline_counts`, then include:

```python
source_trend_claim = str(insight.get("trend_claim") or "none")
source_signal_type = str(insight.get("signal_type") or "not_reportable")
trend_claim, allowed_trends = canonical_trend_claim(len(today_ids), baseline_counts)

row.update({
    "source_trend_claim": source_trend_claim,
    "allowed_aggregate_trend_claims": allowed_trends,
    "trend_claim": trend_claim,
    "source_signal_type": source_signal_type,
    "signal_type": _normalize_signal_type(source_signal_type, trend_claim),
})
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_global_selection.py -q
```

Expected: all global-selection tests PASS after fixture expectations are updated to canonical trends.

- [ ] **Step 5: Commit Task 1**

```bash
git add feedback_hub/topic_discovery/global_selection.py feedback_hub/tests/test_topic_global_selection.py
git commit -m "fix: normalize aggregate daily insight trends"
```

---

### Task 2: Extend Global Selection to Main and Watch Layers

**Files:**
- Modify: `feedback_hub/topic_discovery/global_selection.py:166-374`
- Test: `feedback_hub/tests/test_topic_global_selection.py`

**Interfaces:**
- Consumes: canonical shortlist rows from Task 1.
- Produces: `{selected_insights, watch_insights, selection_summary, novelty_tradeoff}`.

- [ ] **Step 1: Add failing prompt and parser tests**

Update `_selection_reply` to accept watch IDs and always include novelty tradeoff:

```python
def _selection_reply(
    ids: list[str],
    *,
    ranks: list[int] | None = None,
    watch_ids: list[str] | None = None,
) -> str:
    selected = [{
        "insight_id": insight_id,
        "rank": ranks[index] if ranks is not None else index + 1,
        "selection_reason": "相对证据更强",
        "editorial_note": "保持谨慎表达",
        "report_summary": "用户反馈明确问题，需要产品关注",
    } for index, insight_id in enumerate(ids)]
    watch = [{
        "insight_id": insight_id,
        "rank": index,
        "watch_reason": "证据明确，但需要更多天数据验证",
        "editorial_note": "保留证据边界",
    } for index, insight_id in enumerate(watch_ids or [], 1)]
    return json.dumps({
        "selected_insights": selected,
        "watch_insights": watch,
        "selection_summary": "完成全局比较",
        "novelty_tradeoff": "新增信号已进入正文或观察区",
    }, ensure_ascii=False)
```

Add contract tests:

```python
def test_global_prompt_requests_main_watch_and_novelty_tradeoff() -> None:
    prompt = _api().build_global_selection_prompt([_shortlist_row("i1")])
    assert "watch_insights" in prompt
    assert "novelty_tradeoff" in prompt
    assert "persistent backlog" in prompt
    assert "new_signal or reappeared" in prompt


def test_global_parser_accepts_disjoint_main_and_watch_layers() -> None:
    shortlist = [_shortlist_row("i1"), _shortlist_row("i2")]
    result = _api().parse_global_selection_reply(
        _selection_reply(["i1"], watch_ids=["i2"]), shortlist
    )
    assert [row["insight_id"] for row in result["selected_insights"]] == ["i1"]
    assert [row["insight_id"] for row in result["watch_insights"]] == ["i2"]
    assert result["watch_insights"][0]["watch_reason"].startswith("证据明确")
    assert result["novelty_tradeoff"]


def test_global_parser_rejects_main_watch_overlap_and_source_overlap() -> None:
    api = _api()
    shortlist = [_shortlist_row("i1"), _shortlist_row("i2")]
    with pytest.raises(ValueError, match="both main and watch"):
        api.parse_global_selection_reply(_selection_reply(["i1"], watch_ids=["i1"]), shortlist)

    overlapping = [
        _shortlist_row("i1", source_candidate_ids=["c1"]),
        _shortlist_row("i2", source_candidate_ids=["c1"]),
    ]
    with pytest.raises(ValueError, match="overlap"):
        api.parse_global_selection_reply(_selection_reply(["i1"], watch_ids=["i2"]), overlapping)


def test_global_parser_requires_novelty_tradeoff_for_novel_shortlist() -> None:
    payload = json.loads(_selection_reply([], watch_ids=[]))
    payload["novelty_tradeoff"] = ""
    with pytest.raises(ValueError, match="novelty_tradeoff"):
        _api().parse_global_selection_reply(
            json.dumps(payload, ensure_ascii=False), [_shortlist_row("i1")]
        )


def test_global_parser_defaults_missing_watch_for_non_novel_legacy_payload() -> None:
    row = _shortlist_row("i1")
    row["trend_claim"] = "persistent"
    result = _api().parse_global_selection_reply(
        json.dumps({
            "selected_insights": [],
            "selection_summary": "历史空选择",
        }, ensure_ascii=False),
        [row],
    )
    assert result["watch_insights"] == []
    assert result["novelty_tradeoff"] == ""
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_global_selection.py -q
```

Expected: FAIL because the prompt and parser do not support `watch_insights` or `novelty_tradeoff`.

- [ ] **Step 3: Extend prompt, parser, and overlap validation**

Change the prompt so one call produces both layers. Include these exact rules:

```python
"After selecting the main report, select zero to five additional watch_insights.",
"A watch item is not filler. Use it for concrete evidence that needs more days, frequency, or metadata confirmation.",
"Compare persistent backlog with concrete new daily information; frequency alone must not make a persistent backlog item outrank a specific new bug.",
"If a credible new_signal or reappeared item is not main, prefer retaining it in watch_insights so daily novelty is not hidden.",
"When the shortlist contains new_signal or reappeared items, novelty_tradeoff must explain how they were handled.",
```

Return shape:

```python
{
    "selected_insights": [{
        "insight_id": "supplied insight id",
        "rank": 1,
        "selection_reason": "relative reason",
        "editorial_note": "optional evidence boundary",
        "report_summary": "fact-only summary",
    }],
    "watch_insights": [{
        "insight_id": "different supplied insight id",
        "rank": 1,
        "watch_reason": "why it is watched instead of main",
        "editorial_note": "optional evidence boundary",
    }],
    "selection_summary": "overall main-report tradeoff",
    "novelty_tradeoff": "how new and reappeared signals were handled",
}
```

Parser requirements:

```python
watch_values = payload.get("watch_insights", [])
if not isinstance(watch_values, list) or len(watch_values) > 5:
    raise ValueError("watch_insights must contain zero to five items")

selected_ids = {row["insight_id"] for row in normalized_selected}
watch_ids = [str(value.get("insight_id") or "") for value in watch_values]
if selected_ids.intersection(watch_ids):
    raise ValueError("insight cannot appear in both main and watch")
```

Normalize each watch item to `insight_id`, consecutive `rank`, required `watch_reason`, and bounded `editorial_note`. Validate review flags, links, unique IDs, and source overlap across `selected_ids + watch_ids`. Add `"产品影响优先级"` to `_FIX_PRIORITY_PHRASES` so report-attention reasons cannot claim product priority.

Require non-empty `novelty_tradeoff` only when any shortlist row has `trend_claim in {"new_signal", "reappeared"}`. Missing `watch_insights` remains backward-compatible as an empty list.

Update the existing empty-day parser test so its novelty shortlist response includes `novelty_tradeoff`; use the new non-novel test above to cover historical payload compatibility.

For an empty shortlist return:

```python
{
    "selected_insights": [],
    "watch_insights": [],
    "selection_summary": "No eligible shortlist items.",
    "novelty_tradeoff": "No novelty candidates.",
}
```

Bump `prompt_version` to `daily_insight_global_selection_v3_main_watch`.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_global_selection.py -q
```

Expected: all tests PASS, including existing evidence-boundary and route tests.

- [ ] **Step 5: Commit Task 2**

```bash
git add feedback_hub/topic_discovery/global_selection.py feedback_hub/tests/test_topic_global_selection.py
git commit -m "feat: globally select daily insight watch items"
```

---

### Task 3: Partition and Render Product-Readable Report Artifacts

**Files:**
- Modify: `feedback_hub/topic_discovery/insight_artifacts.py:15-337`
- Test: `feedback_hub/tests/test_topic_insight_artifacts.py`

**Interfaces:**
- Consumes: global selection with main/watch layers and the canonical shortlist.
- Produces: `selected["main"]`, `selected["watch"]`, full audit `selected["observe"]`, Markdown report, and XLSX rows with `report_layer=main|watch|manual_review`.

- [ ] **Step 1: Add failing partition and report tests**

Extend `_selection`:

```python
def _selection(*insight_ids: str, watch_ids: tuple[str, ...] = ()) -> dict:
    return {
        "selected_insights": [{
            "insight_id": insight_id,
            "rank": index,
            "selection_reason": f"全局比较后选择{insight_id}",
            "editorial_note": "仅限已知平台",
            "report_summary": f"全局事实摘要{insight_id}",
        } for index, insight_id in enumerate(insight_ids, 1)],
        "watch_insights": [{
            "insight_id": insight_id,
            "rank": index,
            "watch_reason": f"继续验证{insight_id}是否跨日出现",
            "editorial_note": "平台元数据与文本存在冲突",
        } for index, insight_id in enumerate(watch_ids, 1)],
        "selection_summary": "完成全局比较",
        "novelty_tradeoff": "新增信号保留在观察区",
    }
```

Add tests:

```python
def test_partition_uses_only_global_watch_selection_for_report_watch() -> None:
    insights = [
        _insight("i1", candidate_id="c1"),
        _insight("i2", candidate_id="c2"),
        _insight("i3", candidate_id="c3"),
    ]
    selected = artifacts.partition_selected_insights(
        insights,
        {f"c{index}": _candidate(f"c{index}") for index in range(1, 4)},
        _selection("i1", watch_ids=("i2",)),
        shortlist=_shortlist("i1", "i2", "i3"),
    )
    assert [row["insight_id"] for row in selected["main"]] == ["i1"]
    assert [row["insight_id"] for row in selected["watch"]] == ["i2"]
    assert selected["watch"][0]["global_watch_reason"].startswith("继续验证")
    assert [row["insight_id"] for row in selected["observe"]] == ["i3"]


def test_report_answers_four_questions_and_uses_product_copy() -> None:
    insight = _insight("i1", candidate_id="c1")
    insight.update({"signal_type": "new_bug", "trend_claim": "new_signal"})
    candidate = _candidate("c1", links=["https://feedback/main"])
    candidate["baseline_dates"] = ["2026-07-07", "2026-07-08"]
    selected = artifacts.partition_selected_insights(
        [insight], {"c1": candidate}, _selection("i1"), shortlist=_shortlist("i1")
    )

    text = artifacts.render_daily_report(
        "2026-07-14",
        selected,
        [],
        {"baseline_start": "2026-07-07", "baseline_end": "2026-07-13", "route_counts": {"glm": 2}},
        shortlist=selected["main"],
    )

    assert "## 今日结论" in text
    assert "新增或上升问题" in text
    assert "持续问题" in text
    assert "需求机会" in text
    assert "高价值单条" in text
    assert "趋势：新增信号" in text
    assert "平台：Android" in text
    assert "版本：3.5.0" in text
    assert "证据边界：仅限已知平台" in text
    assert "2026-07-07" in text
    assert "模型渠道调用分布" not in text
    assert "new_signal" not in text


def test_report_renders_global_watch_reason_not_local_observe_order() -> None:
    selected = artifacts.partition_selected_insights(
        [_insight("i1", candidate_id="c1")],
        {"c1": _candidate("c1")},
        _selection(watch_ids=("i1",)),
        shortlist=_shortlist("i1"),
    )
    text = artifacts.render_daily_report(
        "2026-07-14", selected, [],
        {"baseline_start": "2026-07-07", "baseline_end": "2026-07-13"},
        shortlist=selected["watch"],
    )
    assert "## 新增信号观察" in text
    assert "继续验证i1是否跨日出现" in text
    assert "平台元数据与文本存在冲突" in text
    assert "未进入今日重点，保留观察" not in text
```

- [ ] **Step 2: Run artifact tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_insight_artifacts.py -q
```

Expected: FAIL because `watch` partitioning and the new report structure do not exist.

- [ ] **Step 3: Implement main/watch partitioning**

In `partition_selected_insights`:

```python
watch_selections = sorted(
    list(global_selection.get("watch_insights") or []),
    key=lambda row: int(row.get("rank") or 0),
)
watch_ids = [str(row.get("insight_id") or "") for row in watch_selections]
```

Validate watch IDs against `by_id` and `shortlist_by_id`, then build rows with:

```python
row.update({
    "report_decision": "watch",
    "global_watch_rank": int(watch_selection["rank"]),
    "global_watch_reason": str(watch_selection.get("watch_reason") or "").strip(),
    "editorial_note": str(watch_selection.get("editorial_note") or "").strip(),
})
```

Exclude both main and watch IDs from the full audit `observe` list. Return all five keys: `main`, `watch`, `observe`, `manual_review`, and `exclude`.

- [ ] **Step 4: Implement product report helpers and workbook layer**

Add labels:

```python
_TREND_LABELS = {
    "new_signal": "新增信号",
    "reappeared": "重新出现",
    "rising": "上升",
    "persistent": "持续出现",
    "repeated": "当日重复",
    "none": "暂无明确趋势",
}
```

Change the renderer signature:

```python
def render_daily_report(
    report_date: str,
    selected: dict[str, list[dict[str, Any]]],
    media_appendix: list[dict[str, Any]],
    run_summary: dict[str, Any],
    *,
    shortlist: list[dict[str, Any]] | None = None,
) -> str:
```

Implement `_render_daily_conclusions(main, watch, shortlist)` so each of the four lines states main conclusions, watch count, and deterministic shortlist candidate count. Implement `_format_baseline(dates, counts)` using `YYYY-MM-DD:count`. Implement `_format_count_keys` for platform/version names. Render `editorial_note` when non-empty.

Render only `selected["watch"]` under `## 新增信号观察`; never slice `selected["observe"]`. Remove route counts from `## 运行说明`.

Change workbook row iteration from:

```python
for layer in ("main", "observe", "manual_review"):
```

to:

```python
for layer in ("main", "watch", "manual_review"):
```

Add audit headers for `source_signal_type`, `source_trend_claim`, `allowed_aggregate_trend_claims`, `global_watch_rank`, and `global_watch_reason`.

- [ ] **Step 5: Run artifact tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_insight_artifacts.py -q
```

Expected: all tests PASS and workbook hyperlinks remain valid.

- [ ] **Step 6: Commit Task 3**

```bash
git add feedback_hub/topic_discovery/insight_artifacts.py feedback_hub/tests/test_topic_insight_artifacts.py
git commit -m "feat: render daily insight conclusions and watchlist"
```

---

### Task 4: Integrate Watch Counts and End-to-End Artifacts

**Files:**
- Modify: `feedback_hub/topic_discovery/daily_insight_run.py:185-230`
- Modify: `scripts/run_daily_insight_signal.py:36-65`
- Test: `feedback_hub/tests/test_daily_insight_run.py`

**Interfaces:**
- Consumes: main/watch partition and shortlist from Tasks 2-3.
- Produces: `watch_insights` in `run_summary.json` and CLI summary; renderer receives full shortlist.

- [ ] **Step 1: Update the fake global response and add failing integration assertions**

Change `_fake_two_stage_call` global response:

```python
return json.dumps({
    "selected_insights": [{
        "insight_id": items[0]["insight_id"],
        "rank": 1,
        "selection_reason": "相对比较后证据最明确",
        "editorial_note": "保持证据边界",
        "report_summary": "用户反馈语音输入后没有文字上屏",
    }],
    "watch_insights": [{
        "insight_id": items[1]["insight_id"],
        "rank": 1,
        "watch_reason": "需要更多天数据确认是否持续",
        "editorial_note": "仅有当日证据",
    }],
    "selection_summary": "本日选择一条正文和一条观察",
    "novelty_tradeoff": "一个新增信号进入正文，另一个保留观察",
}, ensure_ascii=False)
```

Add assertions:

```python
assert summary["main_insights"] == 1
assert summary["watch_insights"] == 1
report = (config.output_dir / "daily_report_draft_20260714.md").read_text()
assert "## 今日结论" in report
assert "## 新增信号观察" in report
assert "模型渠道调用分布" not in report
selection = json.loads((config.output_dir / "global_selection.json").read_text())
assert len(selection["watch_insights"]) == 1
```

Update CLI summary test input and expectation with `"watch_insights": 3`.

- [ ] **Step 2: Run integration tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_daily_insight_run.py::test_run_daily_insight_experiment_writes_complete_artifact_set \
  feedback_hub/tests/test_daily_insight_run.py::test_cli_output_summary_separates_local_and_global_routes -q
```

Expected: FAIL because run summaries do not expose watch counts and the renderer does not receive shortlist.

- [ ] **Step 3: Wire the runner and CLI**

In `daily_insight_run.py` add:

```python
summary.update({
    "insight_decisions": len(insights),
    "main_insights": len(selected["main"]),
    "watch_insights": len(selected["watch"]),
    "observe_insights": len(selected["observe"]),
    "manual_review_insights": len(selected["manual_review"]),
    "excluded_insights": len(selected["exclude"]),
    "media_appendix": len(report_day["media_appendix"]),
})
```

Pass `shortlist=shortlist` to `render_daily_report`. In `scripts/run_daily_insight_signal.py`, add:

```python
"watch_insights": summary.get("watch_insights"),
```

Keep route counts in JSON summary and CLI output; only the product Markdown hides them.

- [ ] **Step 4: Run focused and related tests**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_global_selection.py \
  feedback_hub/tests/test_topic_insight_artifacts.py \
  feedback_hub/tests/test_daily_insight_run.py \
  feedback_hub/tests/test_topic_report_signals.py -q
```

Expected: all tests PASS.

- [ ] **Step 5: Compile changed Python modules**

Run:

```bash
python3 -m py_compile \
  feedback_hub/topic_discovery/global_selection.py \
  feedback_hub/topic_discovery/insight_artifacts.py \
  feedback_hub/topic_discovery/daily_insight_run.py \
  scripts/run_daily_insight_signal.py
```

Expected: exit code 0 with no output.

- [ ] **Step 6: Commit Task 4**

```bash
git add \
  feedback_hub/topic_discovery/daily_insight_run.py \
  scripts/run_daily_insight_signal.py \
  feedback_hub/tests/test_daily_insight_run.py
git commit -m "feat: integrate daily insight watch artifacts"
```

---

### Task 5: Regenerate and Review the 2026-07-14 Report

**Files:**
- Reuse: `feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run/`
- Reuse: `feedback_hub/data/label_v2_samples/feature_knowledge_template_20260701.xlsx`
- Reuse local checkpoints: `outputs/daily_insight_signal_20260714/`
- Generate: `outputs/daily_insight_signal_20260714_v2/`
- Create review: `outputs/daily_insight_signal_20260714_v2/codex_report_quality_review_20260717.md`

**Interfaces:**
- Existing 171 local model calls are resumed from copied checkpoints.
- Only the global-selection checkpoint is moved aside, forcing one v3 global call.
- Old output remains unchanged for side-by-side review.

- [ ] **Step 1: Run the complete backend test suite before real data work**

Run:

```bash
python3 -m pytest feedback_hub/tests -q
```

Expected: all tests PASS. Existing unrelated failures must be reported and isolated before proceeding.

- [ ] **Step 2: Create an independent v2 output directory without deleting old artifacts**

Run:

```bash
cp -R outputs/daily_insight_signal_20260714 outputs/daily_insight_signal_20260714_v2
mkdir -p outputs/daily_insight_signal_20260714_v2/prior_global_selection
for name in \
  global_selection.json \
  global_selection_model_rows.jsonl \
  global_selection_model_rows.jsonl.checkpoint.jsonl \
  global_selection_model_rows.jsonl.failures.jsonl \
  global_selection_model_rows.jsonl.run_state.json; do
  if test -e "outputs/daily_insight_signal_20260714_v2/$name"; then
    mv "outputs/daily_insight_signal_20260714_v2/$name" \
      outputs/daily_insight_signal_20260714_v2/prior_global_selection/
  fi
done
```

Expected: old output is untouched; v2 retains local editor checkpoints but has no active global-selection checkpoint.

- [ ] **Step 3: Resume using environment-only credentials**

Run:

```bash
python3 scripts/run_daily_insight_signal.py \
  --run-root feedback_hub/data/topic_shadow_run_20260707_20260714/v1/run \
  --report-date 2026-07-14 \
  --catalog feedback_hub/data/label_v2_samples/feature_knowledge_template_20260701.xlsx \
  --output-dir outputs/daily_insight_signal_20260714_v2 \
  --route-a-url "$KNOT_AGENT_A_URL" \
  --route-c-url "$KNOT_AGENT_C_URL" \
  --openai-url "$GLM_OPENAI_URL" \
  --openai-model glm-5.2 \
  --resume
```

Expected: 171 local rows are resumed, one global request is processed, and `run_status=completed`. If quota is exhausted, stop with the explicit pause state and do not synthesize a report.

- [ ] **Step 4: Verify trend, selection, links, and copy**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path
from openpyxl import load_workbook

root = Path("outputs/daily_insight_signal_20260714_v2")
load_jsonl = lambda name: [
    json.loads(line) for line in (root / name).read_text().splitlines() if line.strip()
]
shortlist = load_jsonl("global_shortlist.jsonl")
selection = json.loads((root / "global_selection.json").read_text())
main = selection["selected_insights"]
watch = selection["watch_insights"]
by_id = {row["insight_id"]: row for row in shortlist}

assert len(shortlist) <= 30
assert len(main) <= 5 and len(watch) <= 5
assert [row["rank"] for row in main] == list(range(1, len(main) + 1))
assert [row["rank"] for row in watch] == list(range(1, len(watch) + 1))
assert not ({row["insight_id"] for row in main} & {row["insight_id"] for row in watch})
assert selection["novelty_tradeoff"]

ios = next(row for row in shortlist if row["insight_id"] == "insight:insight-rel:0009:01")
languages = next(row for row in shortlist if row["insight_id"] == "insight:insight-rel:0007:01")
assert ios["trend_claim"] == "rising"
assert languages["trend_claim"] == "persistent"

report = (root / "daily_report_draft_20260714.md").read_text()
for text in ("今日结论", "新增或上升问题", "持续问题", "需求机会", "高价值单条"):
    assert text in report
for internal in ("趋势口径：persistent", "趋势口径：none", "模型渠道调用分布"):
    assert internal not in report

book = load_workbook(root / "daily_insight_review.xlsx", data_only=False)
links = [
    cell.hyperlink.target
    for sheet in book.worksheets
    for row in sheet.iter_rows()
    for cell in row
    if cell.hyperlink
]
assert links and all(link.startswith("http") for link in links)
print({"shortlist": len(shortlist), "main": len(main), "watch": len(watch), "links": len(links)})
PY
```

Expected: iOS is `rising`, multilingual demand is `persistent`, main/watch limits hold, and product Markdown contains no internal trend enums or route distribution.

- [ ] **Step 5: Review the regenerated report against source evidence**

Create `codex_report_quality_review_20260717.md` with findings first. Review:

- each main and watch item against representative evidence spans and links;
- whether a persistent high-volume demand displaced a more useful new Bug;
- whether “打字无输出再次出现” appears in main or watch, or is accounted for by `novelty_tradeoff`;
- whether platform/version metadata conflicts are disclosed rather than asserted;
- whether multilingual keyboard and Japanese voice input are clearly distinguished;
- whether the four-line conclusion accurately describes main, watch, and unshown shortlist counts.

- [ ] **Step 6: Run final verification and secret scan**

Run:

```bash
python3 -m pytest feedback_hub/tests -q
python3 -m py_compile \
  feedback_hub/topic_discovery/global_selection.py \
  feedback_hub/topic_discovery/insight_artifacts.py \
  feedback_hub/topic_discovery/daily_insight_run.py \
  scripts/run_daily_insight_signal.py
rg -n --hidden --glob '!*.xlsx' --glob '!*.npz' \
  'sk-[A-Za-z0-9]{16,}|046ab8bd|7ecc8162|b2d9f00f' \
  feedback_hub/topic_discovery feedback_hub/tests scripts \
  outputs/daily_insight_signal_20260714_v2
```

Expected: tests and compilation PASS; secret scan returns no matches.

- [ ] **Step 7: Commit only source and test changes if any post-review fixes were required**

```bash
git status --short
git add \
  feedback_hub/topic_discovery/global_selection.py \
  feedback_hub/topic_discovery/insight_artifacts.py \
  feedback_hub/topic_discovery/daily_insight_run.py \
  feedback_hub/tests/test_topic_global_selection.py \
  feedback_hub/tests/test_topic_insight_artifacts.py \
  feedback_hub/tests/test_daily_insight_run.py \
  scripts/run_daily_insight_signal.py
git diff --cached --check
git commit -m "fix: refine daily report quality"
```

Do not stage `outputs/`, credentials, or unrelated pre-existing changes.
