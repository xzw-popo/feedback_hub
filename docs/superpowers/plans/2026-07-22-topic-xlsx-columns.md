# Topic Feedback XLSX Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify the final feedback workbook by removing four internal columns and placing reviewer rationale at the end of the user-facing worksheet.

**Architecture:** Change only the deterministic XLSX renderer in `feedback_hub/topic_mining/export.py`. Keep verified source rows and machine-readable JSONL/quality artifacts unchanged, while tests define the complete nine-column worksheet contract and hyperlink position.

**Tech Stack:** Python 3.9+, openpyxl, pytest

## Global Constraints

- The `反馈清单` headers must be exactly `反馈时间`, `反馈原文`, `对应链接`, `平台`, `版本`, `设备`, `Feedback ID`, `判定理由`, `证据`, in that order.
- The worksheet must not contain `命中分类`, `Conversation ID`, `Run ID`, or `数据截止时间`.
- `判定理由` and `证据` must be the final two columns.
- The source hyperlink must remain functional in the new third column.
- JSONL, quality-report, manifest, and `导出元数据` fields must remain unchanged.
- Do not modify or stage the user-owned untracked root `WORKSPACE_GUIDE.md`.

---

### Task 1: Update the user-facing workbook column contract

**Files:**
- Modify: `feedback_hub/tests/test_topic_mining_export.py:145-180`
- Modify: `feedback_hub/tests/test_topic_mining_end_to_end.py:295-320`
- Modify: `feedback_hub/topic_mining/export.py:16,139-177`

**Interfaces:**
- Consumes: verified result rows accepted by `_xlsx_bytes(rows, scope) -> bytes`.
- Produces: a `反馈清单` worksheet with the exact nine-column presentation contract; machine-readable artifacts remain untouched.

- [ ] **Step 1: Write the failing export contract test**

Replace the partial header assertion and worksheet cutoff assertion in `test_export_has_unique_ids_links_and_evidence` with the complete contract:

```python
headers = [cell.value for cell in ws[1]]
assert headers == [
    "反馈时间", "反馈原文", "对应链接", "平台", "版本", "设备",
    "Feedback ID", "判定理由", "证据",
]
assert not {
    "命中分类", "Conversation ID", "Run ID", "数据截止时间",
} & set(headers)
assert ws["C2"].hyperlink.target.startswith("https://")
assert ws.cell(2, headers.index("判定理由") + 1).value == "全屏仍显示"
assert ws.cell(2, headers.index("证据") + 1).value == "工具栏一直显示"
```

Keep the existing assertions proving `final_results.jsonl` contains `data_cutoff_ms` and `quality_report.json` contains cutoff and required-field data.

In the end-to-end test, replace the worksheet assertion `assert "数据截止时间" in headers` with:

```python
assert headers == [
    "反馈时间", "反馈原文", "对应链接", "平台", "版本", "设备",
    "Feedback ID", "判定理由", "证据",
]
```

Keep its JSONL and quality-report cutoff assertions unchanged.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_export.py::test_export_has_unique_ids_links_and_evidence \
  feedback_hub/tests/test_topic_mining_end_to_end.py::test_complete_fixture_run_keeps_source_read_only_and_exports_verified_win_matches -q
```

Expected: both tests fail because the worksheet still has thirteen headers and the hyperlink is in column D.

- [ ] **Step 3: Implement the nine-column worksheet**

Change the header constant to:

```python
HEADERS = [
    "反馈时间", "反馈原文", "对应链接", "平台", "版本", "设备",
    "Feedback ID", "判定理由", "证据",
]
```

Change each worksheet row to the same order:

```python
values = [
    _format_time(item.get("ts_ms")), item["text"], _safe_cell(url),
    item.get("platform", ""), item.get("appversion", ""),
    item.get("device_name", ""), item.get("feedback_id", ""),
    row["reason"], "\n".join(row["evidence"]),
]
```

Move the hyperlink to column 3, derive the filter endpoint from the number of headers, and align widths with the new columns:

```python
link = sheet.cell(sheet.max_row, 3)
link.hyperlink = url
link.style = "Hyperlink"

last_column = openpyxl.utils.get_column_letter(len(HEADERS))
sheet.auto_filter.ref = f"A1:{last_column}{max(sheet.max_row, 1)}"
widths = [20, 48, 42, 12, 16, 20, 18, 28, 32]
```

Do not change `_export_row`, JSONL serialization, the quality report, manifest updates, or metadata-sheet generation.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py -q
```

Expected: all export and end-to-end tests pass.

- [ ] **Step 5: Run topic-mining regression and static checks**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py -q
python3 -m compileall -q feedback_hub/topic_mining
git diff --check
```

Expected: all selected tests pass and both static checks exit successfully.

- [ ] **Step 6: Commit**

```bash
git add feedback_hub/topic_mining/export.py \
  feedback_hub/tests/test_topic_mining_export.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py
git commit -m "feat: simplify topic feedback workbook"
```
