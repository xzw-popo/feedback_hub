# Search Report MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an asynchronous search report MVP that saves the current search result `conversation_ids` snapshot, generates a Markdown report in the background, and exposes report creation, listing, detail, and retry flows in the dashboard.

**Architecture:** Add a focused backend report module with a persistent `search_report_job` table, a FastAPI router, and a background runner that samples saved conversation IDs and calls the existing OpenAI-compatible LLM client. Add frontend API bindings plus a report dialog/drawer in `FeedbackList.vue`, reusing the existing search state as the snapshot source.

**Tech Stack:** Python, FastAPI, SQLite/MySQL-compatible SQL helpers, pytest, Vue 3, Element Plus, TypeScript, Vitest.

---

## File Structure

- Create `feedback_hub/search/report_service.py`: persistence helpers, snapshot validation, sample fetching, stats generation, LLM prompt assembly, background job execution.
- Create `feedback_hub/search/report_api.py`: `/api/search-reports` routes and FastAPI background task wiring.
- Modify `feedback_hub/schema.sql`: add SQLite `search_report_job` table and indexes.
- Modify `feedback_hub/schema_mysql.sql`: add MySQL `search_report_job` table and indexes.
- Modify `feedback_hub/api.py`: include the report router.
- Create `feedback_hub/tests/test_search_report.py`: backend tests for create/list/detail/retry/generation failure behavior.
- Modify `dashboard/src/api/feedback.ts`: add report request/response types and functions.
- Modify `dashboard/src/views/FeedbackList.vue`: add report creation entry, dialog, drawer, polling, and Markdown rendering.
- Optionally modify `dashboard/package.json` only if an existing Markdown renderer is unavailable; otherwise render simple Markdown as preformatted text for MVP.

## Task 1: Backend Persistence and API Skeleton

**Files:**
- Modify: `feedback_hub/schema.sql`
- Modify: `feedback_hub/schema_mysql.sql`
- Create: `feedback_hub/search/report_service.py`
- Create: `feedback_hub/search/report_api.py`
- Modify: `feedback_hub/api.py`
- Test: `feedback_hub/tests/test_search_report.py`

- [ ] **Step 1: Write failing backend API tests**

Create `feedback_hub/tests/test_search_report.py` with tests that:

```python
from fastapi.testclient import TestClient

from feedback_hub import db
from feedback_hub.api import create_app


def _seed_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "feedback.db"
    monkeypatch.setattr("feedback_hub.config.DB_PATH", db_path)
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.close()
    return db_path


def test_create_report_job_saves_snapshot(tmp_path, monkeypatch):
    db_path = _seed_schema(tmp_path, monkeypatch)
    client = TestClient(create_app(db_path=str(db_path), frontend_dist=None))

    resp = client.post("/api/search-reports", json={
        "title": "语音输入分析",
        "query": "语音输入不好用",
        "search_type": "smart",
        "filters": {"platform": "android"},
        "search_payload": {"debug": {"regex_patterns": ["语音"]}},
        "conversation_ids": ["c1", "c2"],
        "ai_scores": {"c1": {"score": 3, "reason": "直接相关"}},
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["id"].startswith("report_")

    detail = client.get(f"/api/search-reports/{data['id']}").json()
    assert detail["title"] == "语音输入分析"
    assert detail["conversation_ids"] == ["c1", "c2"]
    assert detail["filters"]["platform"] == "android"


def test_create_report_job_rejects_empty_snapshot(tmp_path, monkeypatch):
    db_path = _seed_schema(tmp_path, monkeypatch)
    client = TestClient(create_app(db_path=str(db_path), frontend_dist=None))

    resp = client.post("/api/search-reports", json={
        "title": "空报告",
        "search_type": "smart",
        "conversation_ids": [],
    })

    assert resp.status_code == 400
    assert "conversation_ids" in resp.json()["detail"]


def test_list_report_jobs_returns_newest_first(tmp_path, monkeypatch):
    db_path = _seed_schema(tmp_path, monkeypatch)
    client = TestClient(create_app(db_path=str(db_path), frontend_dist=None))

    first = client.post("/api/search-reports", json={
        "title": "第一份",
        "search_type": "keyword",
        "conversation_ids": ["c1"],
    }).json()
    second = client.post("/api/search-reports", json={
        "title": "第二份",
        "search_type": "keyword",
        "conversation_ids": ["c2"],
    }).json()

    items = client.get("/api/search-reports").json()["items"]
    assert [item["id"] for item in items[:2]] == [second["id"], first["id"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest feedback_hub/tests/test_search_report.py -q`

Expected: FAIL because `/api/search-reports` routes and table do not exist.

- [ ] **Step 3: Implement schema, service persistence, and API skeleton**

Add `search_report_job` DDL to both schemas. Implement:

```python
def create_report_job(payload: CreateReportPayload) -> dict
def list_report_jobs(limit: int = 20) -> list[dict]
def get_report_job(job_id: str) -> dict | None
```

Implement `report_api.py` with:

```python
router = APIRouter(prefix="/api/search-reports", tags=["search-reports"])
@router.post("")
@router.get("")
@router.get("/{job_id}")
```

Include `report_router` in `feedback_hub/api.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest feedback_hub/tests/test_search_report.py -q`

Expected: PASS for the three skeleton tests.

- [ ] **Step 5: Commit**

Run:

```bash
git add feedback_hub/schema.sql feedback_hub/schema_mysql.sql feedback_hub/search/report_service.py feedback_hub/search/report_api.py feedback_hub/api.py feedback_hub/tests/test_search_report.py
git commit -m "feat: add search report job api"
```

## Task 2: Backend Report Generation and Retry

**Files:**
- Modify: `feedback_hub/search/report_service.py`
- Modify: `feedback_hub/search/report_api.py`
- Test: `feedback_hub/tests/test_search_report.py`

- [ ] **Step 1: Write failing generation tests**

Append tests that:

```python
def _seed_conversation(conn, cid, text, score_ts):
    conn.execute(
        "INSERT INTO conversation_label "
        "(conversation_id, L1, L2, severity, confidence, reason, source, msg_count, "
        "first_ts_ms, last_ts_ms, user_vid, appversion, channel, aggregated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, "A.Bug", "语音", "P1", 0.8, "reason", "aggregated", 1,
         score_ts, score_ts, "u", "8.2.1", "test", score_ts),
    )
    conn.execute(
        "INSERT INTO feedback "
        "(feedback_id, conversation_id, msg_seq, channel, ts_ms, platform, appversion, "
        "user_vid, keyboard_source, device_name, channelid, enginever, msgtype, text, "
        "tags, raw_json, pulled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"{cid}-f1", cid, 0, "test", score_ts, "android", "8.2.1",
         "u", None, None, None, None, "text", text, "", "{}", score_ts),
    )


def test_run_report_job_generates_markdown_from_snapshot(tmp_path, monkeypatch):
    db_path = _seed_schema(tmp_path, monkeypatch)
    conn = db.connect(db_path)
    _seed_conversation(conn, "c1", "语音输入没有反应", 1000)
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "feedback_hub.search.report_service.chat_completion",
        lambda messages, **kwargs: "# 搜索反馈分析报告\n\n## 结论摘要\n基于快照。",
    )
    client = TestClient(create_app(db_path=str(db_path), frontend_dist=None))
    job = client.post("/api/search-reports", json={
        "title": "语音输入分析",
        "query": "语音输入不好用",
        "search_type": "smart",
        "conversation_ids": ["c1"],
    }).json()

    from feedback_hub.search.report_service import run_report_job
    run_report_job(job["id"], db_path=str(db_path))

    detail = client.get(f"/api/search-reports/{job['id']}").json()
    assert detail["status"] == "succeeded"
    assert "搜索反馈分析报告" in detail["result_markdown"]
    assert detail["sample_count"] == 1


def test_retry_failed_report_reuses_snapshot(tmp_path, monkeypatch):
    db_path = _seed_schema(tmp_path, monkeypatch)
    client = TestClient(create_app(db_path=str(db_path), frontend_dist=None))
    job = client.post("/api/search-reports", json={
        "title": "重试报告",
        "search_type": "keyword",
        "conversation_ids": ["missing"],
    }).json()

    from feedback_hub.search.report_service import mark_report_failed
    mark_report_failed(job["id"], "boom", db_path=str(db_path))

    resp = client.post(f"/api/search-reports/{job['id']}/retry")
    assert resp.status_code == 200
    detail = client.get(f"/api/search-reports/{job['id']}").json()
    assert detail["status"] in ("pending", "running", "failed")
    assert detail["conversation_ids"] == ["missing"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest feedback_hub/tests/test_search_report.py -q`

Expected: FAIL because generation and retry are not implemented.

- [ ] **Step 3: Implement generation and retry**

Implement:

```python
def run_report_job(job_id: str, db_path: str | None = None) -> None
def mark_report_failed(job_id: str, message: str, db_path: str | None = None) -> None
def retry_report_job(job_id: str, db_path: str | None = None) -> dict
```

Use `chat_completion` from `feedback_hub.search.llm_client`. Fetch texts by snapshot IDs, sort by `ai_scores` descending then `last_ts_ms` descending, cap samples at 80 and text at 500 chars. On failure, mark status `failed`.

Wire `POST /api/search-reports/{id}/retry` and FastAPI `BackgroundTasks` for create/retry.

- [ ] **Step 4: Run backend tests**

Run: `pytest feedback_hub/tests/test_search_report.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add feedback_hub/search/report_service.py feedback_hub/search/report_api.py feedback_hub/tests/test_search_report.py
git commit -m "feat: generate async search reports"
```

## Task 3: Frontend API and UI

**Files:**
- Modify: `dashboard/src/api/feedback.ts`
- Modify: `dashboard/src/views/FeedbackList.vue`

- [ ] **Step 1: Write failing frontend/API test if feasible**

Inspect existing Vitest style. Add tests only if current helpers can cover the new API without large harness work. At minimum run TypeScript build after implementation.

- [ ] **Step 2: Add API bindings**

Add TypeScript types and functions:

```ts
export interface CreateSearchReportRequest {
  title: string
  query?: string
  search_type: 'smart' | 'keyword' | 'mixed'
  filters?: MetadataFilters
  search_payload?: Record<string, unknown>
  conversation_ids: string[]
  ai_scores?: Record<string, { score: 1 | 2 | 3; reason: string }>
}
export interface SearchReportJob {
  id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  title: string
  query: string | null
  search_type: string
  sample_count: number
  result_markdown: string | null
  error_message: string | null
  created_at: number
  started_at: number | null
  finished_at: number | null
  conversation_ids?: string[]
}
```

Implement `createSearchReport`, `listSearchReports`, `getSearchReport`, `retrySearchReport`.

- [ ] **Step 3: Add FeedbackList report UI**

Add:

- “生成报告” button near search results.
- Create confirmation dialog with title, result count, snapshot count, and async warning.
- Report drawer with task list and detail view.
- Polling every 5 seconds while any task is `pending` or `running`.
- Copy Markdown action using `navigator.clipboard.writeText`.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
cd dashboard
npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add dashboard/src/api/feedback.ts dashboard/src/views/FeedbackList.vue
git commit -m "feat: add search report dashboard flow"
```

## Task 4: End-to-End Verification

**Files:**
- No new files unless bug fixes are required.

- [ ] **Step 1: Run backend tests**

Run: `pytest feedback_hub/tests/test_search_report.py feedback_hub/tests/test_api.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd dashboard
npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run full relevant test suite if time permits**

Run: `pytest -q`

Expected: PASS or report unrelated existing failures.

- [ ] **Step 4: Final commit if verification fixes were needed**

Run:

```bash
git status --short
git add <changed-files>
git commit -m "fix: stabilize search report mvp"
```

Only commit if files changed during verification.

## Self-Review

- Spec coverage: task snapshot creation, async generation, list/detail/retry, weak-label report generation, frontend dialog/drawer, polling, copy Markdown, and timeout/error handling are covered.
- Scope: no active insight, no report deletion, no permissions, no external queue.
- Placeholder scan: no `TBD` or undecided implementation requirements remain.
- Type consistency: backend uses `conversation_ids`, `ai_scores`, `result_markdown`; frontend types mirror those names.
