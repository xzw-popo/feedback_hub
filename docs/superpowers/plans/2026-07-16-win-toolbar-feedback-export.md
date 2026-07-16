# Win Toolbar Feedback Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a verified one-off Excel list of Win feedback from 2026-01-16 through the 2026-07-16 execution snapshot for toolbar position drift and toolbar visibility during full-screen video or games, including borderless-window and exclusive full-screen modes.

**Architecture:** Take a consistent snapshot of the current remote SQLite database, backfill only the missing historical window into that local copy, and leave both production databases untouched. Run deterministic broad recall followed by batched OpenAI-compatible semantic classification, then export only matched feedback IDs to a formatted workbook.

**Tech Stack:** Python 3, SQLite, existing `feedback_hub.puller`, existing OpenAI-compatible LLM configuration, `openpyxl`, JSONL checkpoints.

## Global Constraints

- Work only under `feedback_hub/data/win_toolbar_feedback_20260716/`; this directory is ignored by Git.
- Do not modify or replace local `feedback_hub/data/feedback.db`.
- Do not upload a database, restart DevCloud, or modify the remote production database.
- Target platform is exactly `Win`.
- Target time begins at `2026-01-16 00:00:00` local time and ends at the recorded remote snapshot time.
- Issue 2 includes video or games in full-screen, borderless-window full-screen, or exclusive full-screen modes; ordinary always-on-top behavior without a video or game full-screen context is excluded.
- General L1/L2/severity labels do not affect inclusion.
- Final rows are unique by `feedback_id` and must contain a usable conversation URL.

---

### Task 1: Build and validate the local six-month source snapshot

**Files:**
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/source.db`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/run_export.py`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/test_run_export.py`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/pull_manifest.json`

**Interfaces:**
- Consumes: remote `/opt/feedback_hub/feedback_hub/data/feedback.db`, `feedback_hub.puller.pull(start_dt, end_dt, conn=conn)`
- Produces: `source.db` covering the target window and a manifest with `snapshot_at`, `completed_dates`, `failed_dates`, and per-day row counts

- [ ] **Step 1: Write failing tests for date windows and coverage validation**

```python
from datetime import datetime
from run_export import daily_windows, validate_coverage


def test_daily_windows_are_half_open_and_complete():
    windows = daily_windows(datetime(2026, 1, 16), datetime(2026, 1, 18))
    assert windows == [
        (datetime(2026, 1, 16), datetime(2026, 1, 17)),
        (datetime(2026, 1, 17), datetime(2026, 1, 18)),
    ]


def test_validate_coverage_rejects_missing_day():
    try:
        validate_coverage(["2026-01-16"], datetime(2026, 1, 16), datetime(2026, 1, 18))
    except ValueError as exc:
        assert "2026-01-17" in str(exc)
    else:
        raise AssertionError("missing date was accepted")
```

- [ ] **Step 2: Run tests and verify the expected import failure**

Run: `cd feedback_hub/data/win_toolbar_feedback_20260716 && python3 -m pytest test_run_export.py -q`

Expected: FAIL because `daily_windows` and `validate_coverage` do not exist.

- [ ] **Step 3: Implement the minimal window and manifest helpers**

```python
def daily_windows(start, end):
    out = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=1), end)
        out.append((cursor, nxt))
        cursor = nxt
    return out


def validate_coverage(completed_dates, start, end):
    expected = {s.strftime("%Y-%m-%d") for s, _ in daily_windows(start, end)}
    missing = sorted(expected - set(completed_dates))
    if missing:
        raise ValueError(f"missing pull dates: {', '.join(missing)}")
```

- [ ] **Step 4: Create and download a consistent remote SQLite snapshot**

Run:

```bash
mkdir -p feedback_hub/data/win_toolbar_feedback_20260716
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com "sqlite3 /opt/feedback_hub/feedback_hub/data/feedback.db '.backup /tmp/win_toolbar_feedback_20260716.db'"
scp -P 36000 root@charvelxia-any2.devcloud.woa.com:/tmp/win_toolbar_feedback_20260716.db feedback_hub/data/win_toolbar_feedback_20260716/source.db
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com "rm -f /tmp/win_toolbar_feedback_20260716.db"
```

Expected: local `source.db` passes `PRAGMA integrity_check` and has a maximum `feedback.ts_ms` matching the recorded snapshot time.

- [ ] **Step 5: Backfill each missing day with checkpointed retries**

Implement `backfill(db_path, start, end, manifest_path)` so it opens `source.db`, calls `puller.pull` for each incomplete day from `2026-01-16` through `2026-04-21`, records the result immediately, and retries an individual failed date up to three times. Do not call `tagger.pipeline.run_tagging`.

Run: `python3 feedback_hub/data/win_toolbar_feedback_20260716/run_export.py backfill`

Expected: `failed_dates` is empty and coverage validation reports every date complete.

- [ ] **Step 6: Verify source integrity and scope**

Run:

```bash
sqlite3 -readonly feedback_hub/data/win_toolbar_feedback_20260716/source.db "PRAGMA integrity_check; SELECT MIN(datetime(ts_ms/1000,'unixepoch','localtime')), MAX(datetime(ts_ms/1000,'unixepoch','localtime')) FROM feedback; SELECT COUNT(*) FROM feedback WHERE platform='Win' AND ts_ms >= strftime('%s','2026-01-16')*1000;"
```

Expected: `ok`, minimum data time no later than `2026-01-16`, and a non-zero Win count.

### Task 2: Recall and semantically classify the two issue types

**Files:**
- Modify: `feedback_hub/data/win_toolbar_feedback_20260716/run_export.py`
- Modify: `feedback_hub/data/win_toolbar_feedback_20260716/test_run_export.py`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/candidates.jsonl`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/classified.jsonl`

**Interfaces:**
- Consumes: `source.db`
- Produces: one classification record per candidate with `feedback_id`, `matched`, `issue_type`, `confidence`, and `reason`

- [ ] **Step 1: Write failing recall and parser tests**

```python
from run_export import recall_issue_types, parse_classification


def test_recall_toolbar_position_drift():
    assert "工具栏位置错乱" in recall_issue_types("工具栏又自己跑到左下角了")


def test_recall_fullscreen_game_without_literal_toolbar():
    assert "视频或游戏全屏不隐藏" in recall_issue_types("打 LOL 的时候这个输入法框一直置顶挡画面")


def test_recall_video_exclusive_fullscreen():
    assert "视频或游戏全屏不隐藏" in recall_issue_types("播放器独占全屏看视频时工具栏还在")


def test_parse_fullscreen_video_match():
    result = parse_classification('{"matched":true,"issue_type":"视频或游戏全屏不隐藏","confidence":0.98,"reason":"视频独占全屏仍显示"}')
    assert result["matched"] is True
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd feedback_hub/data/win_toolbar_feedback_20260716 && python3 -m pytest test_run_export.py -q`

Expected: FAIL because recall and classification parsing are not implemented.

- [ ] **Step 3: Implement deterministic broad recall**

Implement `recall_issue_types(text: str) -> set[str]` using two intentionally broad groups:

```python
TOOLBAR_OBJECTS = ("工具栏", "悬浮栏", "悬浮窗", "浮窗", "状态栏", "输入法框", "输入栏")
POSITION_SIGNALS = ("位置", "左下", "右上", "乱跑", "跑到", "错位", "漂", "重置", "固定", "多屏", "双屏", "跨屏")
FULLSCREEN_CONTEXTS = ("游戏", "打游戏", "LOL", "英雄联盟", "原神", "CS2", "Steam", "视频", "看视频", "播放", "播放器", "观影", "直播")
VISIBILITY_SIGNALS = ("全屏", "无边框", "独占", "隐藏", "置顶", "挡", "遮", "误触", "关不掉", "一直显示")
```

Recall issue 1 when a toolbar object and position signal co-occur. Recall issue 2 when a video/game context and visibility or full-screen-mode signal co-occur. Retain the union so semantic classification can remove false positives.

- [ ] **Step 4: Add 30-minute user context and batched semantic classification**

Query candidates with exact platform and time bounds. For each candidate, attach same-user messages within `candidate.ts_ms ± 1,800,000` milliseconds. Call `feedback_hub.search.llm_client.chat_completion` in batches of at most 20 and require JSON output matching:

```json
{"results":[{"feedback_id":"id","matched":true,"issue_type":"工具栏位置错乱","confidence":0.97,"reason":"工具栏在双屏切换后跑到左下角"}]}
```

The prompt must explicitly reject position-setting questions without abnormal movement and ordinary always-on-top behavior without a video or game full-screen context. It must accept video, live-stream, and game contexts in normal, borderless-window, or exclusive full-screen modes. Append each parsed result to `classified.jsonl` immediately so reruns skip completed IDs.

- [ ] **Step 5: Run classification and verify invariants**

Run: `python3 feedback_hub/data/win_toolbar_feedback_20260716/run_export.py classify`

Expected: every candidate ID has exactly one valid classification record; matched records use only `工具栏位置错乱` or `视频或游戏全屏不隐藏`.

- [ ] **Step 6: Review boundary samples**

Export deterministic samples of high-confidence matches, low-confidence matches, and rejects containing ordinary window behavior or position-setting questions. Include accepted samples for video, games, borderless-window full-screen, and exclusive full-screen. Correct only demonstrable classification errors through a recorded `review_overrides.jsonl` keyed by `feedback_id`; never edit source text.

### Task 3: Generate and verify the Excel deliverable

**Files:**
- Modify: `feedback_hub/data/win_toolbar_feedback_20260716/run_export.py`
- Modify: `feedback_hub/data/win_toolbar_feedback_20260716/test_run_export.py`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/Win端工具栏问题反馈_近半年_20260716.xlsx`
- Create: `feedback_hub/data/win_toolbar_feedback_20260716/quality_report.json`

**Interfaces:**
- Consumes: `source.db`, `classified.jsonl`, optional `review_overrides.jsonl`
- Produces: final `.xlsx` and machine-readable quality report

- [ ] **Step 1: Write failing tests for row construction and URL fallback**

```python
from run_export import build_chat_url, validate_export_rows


def test_build_chat_url_encodes_required_identity():
    url = build_chat_url("wetype", 10000, "123")
    assert url == "https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=123"


def test_validate_export_rows_rejects_duplicate_feedback_id():
    row = {"feedback_id": "x", "platform": "Win", "text": "反馈", "url": "https://example.test", "issue_type": "工具栏位置错乱"}
    try:
        validate_export_rows([row, row])
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate feedback_id was accepted")
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd feedback_hub/data/win_toolbar_feedback_20260716 && python3 -m pytest test_run_export.py -q`

Expected: FAIL because URL and export validation helpers are missing.

- [ ] **Step 3: Implement final row validation and workbook creation**

Create one `反馈清单` sheet with columns `问题类型`, `反馈时间`, `反馈原文`, `对应会话链接`, `Win 版本`, `设备信息`, and `反馈 ID`. Sort by issue type and descending timestamp; freeze `A2`, enable auto-filter, wrap feedback text, use Arial consistently, and set the URL cell as a clickable hyperlink.

- [ ] **Step 4: Generate the workbook**

Run: `python3 feedback_hub/data/win_toolbar_feedback_20260716/run_export.py export`

Expected: workbook and `quality_report.json` are created; the report records row counts by issue type, date bounds, duplicate count, missing URL count, and invalid platform count.

- [ ] **Step 5: Verify workbook structure and content**

Run a read-only `openpyxl.load_workbook` verification that checks the exact sheet name and headers, every hyperlink target, unique feedback IDs, both allowed issue types, and chronological bounds.

Expected: all checks pass with zero duplicates, zero missing links, and zero invalid platform rows.

- [ ] **Step 6: Visually inspect the rendered workbook**

Open the generated file with LibreOffice in headless conversion mode to PDF or use the bundled spreadsheet rendering runtime, inspect the rendered first page and representative long-text rows, then adjust widths or wrapping if any content is clipped or unreadable.

### Task 4: Final end-to-end audit and handoff

**Files:**
- Verify: `feedback_hub/data/win_toolbar_feedback_20260716/pull_manifest.json`
- Verify: `feedback_hub/data/win_toolbar_feedback_20260716/quality_report.json`
- Verify: `feedback_hub/data/win_toolbar_feedback_20260716/Win端工具栏问题反馈_近半年_20260716.xlsx`

**Interfaces:**
- Consumes: all task outputs
- Produces: a user-facing handoff with the workbook path, exact snapshot interval, and final counts

- [ ] **Step 1: Run all local helper tests**

Run: `cd feedback_hub/data/win_toolbar_feedback_20260716 && python3 -m pytest test_run_export.py -q`

Expected: all tests PASS.

- [ ] **Step 2: Re-run database and workbook validation**

Run: `python3 feedback_hub/data/win_toolbar_feedback_20260716/run_export.py verify`

Expected: `status=success`, no pull gaps, SQLite integrity `ok`, no duplicate feedback IDs, no invalid issue types, no invalid platforms, and no missing URLs.

- [ ] **Step 3: Confirm production was untouched**

Compare local production database size and modification time captured before execution, and query the remote service status and remote database modification time without writing to either location.

Expected: the local formal database and remote database were not replaced by this workflow; any remote growth is attributable only to its independent daily synchronization.

- [ ] **Step 4: Deliver the workbook**

Provide a clickable link to the `.xlsx`, the two final row counts, the precise source snapshot end time, and any disclosed limitations. Do not commit files under `feedback_hub/data/`.
