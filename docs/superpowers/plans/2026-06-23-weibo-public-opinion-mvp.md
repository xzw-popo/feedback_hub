# Weibo Public Opinion MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first independent Weibo public opinion module: schema, normalization/storage, rule labels, read APIs, and Dashboard overview/list pages.

**Architecture:** Add a focused `feedback_hub.weibo` package that owns Weibo schema, storage, labels, stats, and API routing. Keep Weibo data separate from the existing APP feedback tables while exposing `/api/weibo/*` endpoints consumed by new Vue pages under `/weibo`.

**Tech Stack:** Python 3, FastAPI, SQLite via existing `feedback_hub.db`, pytest, Vue 3, TypeScript, Element Plus, ECharts, Vitest.

---

## File Structure

- Create `feedback_hub/weibo/__init__.py`: package marker.
- Create `feedback_hub/weibo/schema.sql`: SQLite DDL for `weibo_post`, `weibo_hit`, `weibo_label`, `weibo_crawl_run`.
- Create `feedback_hub/weibo/models.py`: typed constants and small row helpers.
- Create `feedback_hub/weibo/store.py`: schema initialization, upsert helpers, list/stats queries.
- Create `feedback_hub/weibo/labels.py`: deterministic rule classifier for brand focus, sentiment, topics, post type, and risk.
- Create `feedback_hub/weibo/api.py`: FastAPI router for stats, posts, post detail, and crawl runs.
- Modify `feedback_hub/api.py`: include the Weibo router.
- Create `feedback_hub/tests/test_weibo_labels.py`: rule label coverage.
- Create `feedback_hub/tests/test_weibo_store.py`: schema/upsert/query coverage.
- Create `feedback_hub/tests/test_weibo_api.py`: API contract coverage.
- Create `dashboard/src/api/weibo.ts`: TypeScript client and response types.
- Create `dashboard/src/constants/weibo.ts`: enum labels and display helpers.
- Create `dashboard/src/views/WeiboOverview.vue`: Weibo KPI/trend/distribution page.
- Create `dashboard/src/views/WeiboList.vue`: searchable/filterable Weibo post list.
- Modify `dashboard/src/router/index.ts`: add `/weibo` and `/weibo/list`.
- Modify `dashboard/src/App.vue`: add “微博舆情” navigation.
- Create `dashboard/src/__tests__/weibo-api.test.ts`: API adapter tests.
- Create `dashboard/src/__tests__/router.test.ts`: extend existing route test if needed.

## Task 1: Weibo Rule Labels

**Files:**
- Create: `feedback_hub/weibo/__init__.py`
- Create: `feedback_hub/weibo/models.py`
- Create: `feedback_hub/weibo/labels.py`
- Test: `feedback_hub/tests/test_weibo_labels.py`

- [ ] **Step 1: Write failing tests**

```python
from feedback_hub.weibo.labels import classify_post


def test_classifies_wechat_and_doubao_comparison():
    label = classify_post("微信输入法和豆包输入法比起来，豆包 AI 候选更智能")
    assert label["brand_focus"] == "comparison"
    assert label["sentiment"] == "positive"
    assert "feature_comparison" in label["topics"]
    assert label["risk_level"] == "normal"


def test_classifies_negative_wechat_risk():
    label = classify_post("微信键盘最近广告太烦了，还担心隐私被偷听")
    assert label["brand_focus"] == "wechat"
    assert label["sentiment"] == "negative"
    assert "ads" in label["topics"]
    assert "privacy" in label["topics"]
    assert label["risk_level"] == "watch"


def test_classifies_doubao_ai_topic():
    label = classify_post("豆包输入法的 AI 改写挺好用，推荐试试")
    assert label["brand_focus"] == "doubao"
    assert label["sentiment"] == "positive"
    assert "ai_capability" in label["topics"]
    assert label["post_type"] == "recommendation"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_labels.py -q`
Expected: FAIL because `feedback_hub.weibo.labels` does not exist.

- [ ] **Step 3: Implement minimal label classifier**

Create enum constants in `models.py` and implement `classify_post(text: str) -> dict[str, object]` in `labels.py`. Use deterministic keyword rules for MVP.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_labels.py -q`
Expected: PASS.

## Task 2: Weibo Schema And Store

**Files:**
- Create: `feedback_hub/weibo/schema.sql`
- Create: `feedback_hub/weibo/store.py`
- Test: `feedback_hub/tests/test_weibo_store.py`

- [ ] **Step 1: Write failing store tests**

```python
import sqlite3

from feedback_hub.weibo.store import (
    init_schema,
    list_posts,
    get_stats,
    upsert_post,
)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_upsert_post_creates_post_hit_and_label():
    conn = make_conn()
    result = upsert_post(
        conn,
        {
            "weibo_id": "1001",
            "url": "https://weibo.com/1001",
            "author": {"user_id": "u1", "screen_name": "用户A", "verified": True},
            "created_at_raw": "2026-06-23 10:00",
            "created_at_ms": 1782180000000,
            "text": "微信输入法和豆包输入法比起来，豆包 AI 更好用",
            "pic_urls": [],
            "reposts_count": 1,
            "comments_count": 2,
            "attitudes_count": 3,
            "raw": {"id": "1001"},
        },
        keyword="微信输入法 豆包",
        query_name="微信输入法 豆包",
        searched_at=1782180100,
        search_rank=1,
        source_mode="pc",
    )
    assert result["inserted"] is True
    posts = list_posts(conn, brand_focus="comparison", limit=20, offset=0)
    assert posts["total"] == 1
    assert posts["items"][0]["keywords"] == ["微信输入法 豆包"]
    assert posts["items"][0]["brand_focus"] == "comparison"


def test_stats_counts_brand_sentiment_and_latest_run():
    conn = make_conn()
    upsert_post(conn, {"weibo_id": "1002", "url": "https://weibo.com/1002", "text": "豆包输入法 AI 好用", "raw": {}}, keyword="豆包输入法")
    stats = get_stats(conn)
    assert stats["total_posts"] == 1
    assert stats["brand_focus_counts"]["doubao"] == 1
    assert stats["sentiment_counts"]["positive"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_store.py -q`
Expected: FAIL because store functions do not exist.

- [ ] **Step 3: Implement schema and store**

Implement SQLite schema loading, post upsert, hit insert, rule label upsert, list filters, detail lookup, crawl run listing, and aggregate stats.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_store.py -q`
Expected: PASS.

## Task 3: Weibo FastAPI Router

**Files:**
- Create: `feedback_hub/weibo/api.py`
- Modify: `feedback_hub/api.py`
- Test: `feedback_hub/tests/test_weibo_api.py`

- [ ] **Step 1: Write failing API tests**

```python
from fastapi.testclient import TestClient

from feedback_hub.api import create_app
from feedback_hub.weibo.store import init_schema, upsert_post
from feedback_hub import db


def test_weibo_posts_api_filters_by_brand(tmp_path):
    db_path = tmp_path / "feedback.db"
    conn = db.connect(db_path)
    init_schema(conn)
    upsert_post(conn, {"weibo_id": "2001", "url": "https://weibo.com/2001", "text": "豆包输入法 AI 好用", "raw": {}}, keyword="豆包输入法")
    conn.close()
    client = TestClient(create_app(db_path=str(db_path)))
    resp = client.get("/api/weibo/posts", params={"brand_focus": "doubao"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["brand_focus"] == "doubao"


def test_weibo_stats_api_returns_counts(tmp_path):
    db_path = tmp_path / "feedback.db"
    conn = db.connect(db_path)
    init_schema(conn)
    upsert_post(conn, {"weibo_id": "2002", "url": "https://weibo.com/2002", "text": "微信键盘广告太烦", "raw": {}}, keyword="微信键盘")
    conn.close()
    client = TestClient(create_app(db_path=str(db_path)))
    resp = client.get("/api/weibo/stats")
    assert resp.status_code == 200
    assert resp.json()["brand_focus_counts"]["wechat"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_api.py -q`
Expected: FAIL because `/api/weibo/*` routes do not exist.

- [ ] **Step 3: Implement router and include it**

Add `router = APIRouter(prefix="/api/weibo", tags=["weibo"])` with `GET /stats`, `GET /posts`, `GET /posts/{id}`, `GET /crawl-runs`. Include it in `create_app`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_api.py -q`
Expected: PASS.

## Task 4: Frontend API Client And Routes

**Files:**
- Create: `dashboard/src/api/weibo.ts`
- Create: `dashboard/src/constants/weibo.ts`
- Modify: `dashboard/src/router/index.ts`
- Modify: `dashboard/src/App.vue`
- Test: `dashboard/src/__tests__/weibo-api.test.ts`
- Test: `dashboard/src/__tests__/router.test.ts`

- [ ] **Step 1: Write failing frontend tests**

```ts
import { describe, expect, it, vi } from 'vitest'
import { listWeiboPosts, getWeiboStats } from '@/api/weibo'
import { http } from '@/api/http'

vi.mock('@/api/http', () => ({
  http: {
    get: vi.fn(),
  },
}))

describe('weibo api', () => {
  it('lists posts with cleaned params', async () => {
    vi.mocked(http.get).mockResolvedValueOnce({ data: { total: 0, items: [] } })
    await listWeiboPosts({ q: '豆包', brand_focus: '', limit: 20, offset: 0 })
    expect(http.get).toHaveBeenCalledWith('/api/weibo/posts', {
      params: { q: '豆包', limit: 20, offset: 0 },
    })
  })

  it('fetches stats', async () => {
    vi.mocked(http.get).mockResolvedValueOnce({ data: { total_posts: 0 } })
    await getWeiboStats({ from: '2026-06-01', to: '' })
    expect(http.get).toHaveBeenCalledWith('/api/weibo/stats', {
      params: { from: '2026-06-01' },
    })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- src/__tests__/weibo-api.test.ts`
Expected: FAIL because `@/api/weibo` does not exist.

- [ ] **Step 3: Implement API client, labels, routes, and nav**

Implement typed API functions and add `/weibo` and `/weibo/list` routes. Add the “微博舆情” nav item.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- src/__tests__/weibo-api.test.ts src/__tests__/router.test.ts`
Expected: PASS.

## Task 5: Weibo Overview And List Pages

**Files:**
- Create: `dashboard/src/views/WeiboOverview.vue`
- Create: `dashboard/src/views/WeiboList.vue`
- Modify: `dashboard/src/router/index.ts`

- [ ] **Step 1: Write or extend route smoke test**

Assert `/weibo` resolves to `weibo-overview` and `/weibo/list` resolves to `weibo-list`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- src/__tests__/router.test.ts`
Expected: FAIL until routes/pages exist.

- [ ] **Step 3: Implement pages**

`WeiboOverview.vue` fetches stats and renders KPI cards, brand/sentiment/topic/risk sections, trend table, latest crawl state, and high-risk posts. `WeiboList.vue` syncs filters through URL query, fetches `/api/weibo/posts`, and renders searchable results with source links.

- [ ] **Step 4: Run frontend tests**

Run: `cd dashboard && npm test -- src/__tests__/router.test.ts src/__tests__/weibo-api.test.ts`
Expected: PASS.

## Task 6: Verification

**Files:**
- All files above.

- [ ] **Step 1: Run focused backend tests**

Run: `python3 -m pytest feedback_hub/tests/test_weibo_labels.py feedback_hub/tests/test_weibo_store.py feedback_hub/tests/test_weibo_api.py -q`
Expected: PASS.

- [ ] **Step 2: Run focused frontend tests**

Run: `cd dashboard && npm test -- src/__tests__/weibo-api.test.ts src/__tests__/router.test.ts`
Expected: PASS.

- [ ] **Step 3: Run lint/build if focused tests pass**

Run: `cd dashboard && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit implementation**

```bash
git add feedback_hub/weibo feedback_hub/api.py feedback_hub/tests/test_weibo_*.py dashboard/src/api/weibo.ts dashboard/src/constants/weibo.ts dashboard/src/views/WeiboOverview.vue dashboard/src/views/WeiboList.vue dashboard/src/router/index.ts dashboard/src/App.vue dashboard/src/__tests__/weibo-api.test.ts dashboard/src/__tests__/router.test.ts docs/superpowers/plans/2026-06-23-weibo-public-opinion-mvp.md
git commit -m "feat: add weibo public opinion mvp"
```

## Self-Review

- Spec coverage: MVP covers independent schema, background-ready storage, rule labels, stats/list/detail/crawl-run APIs, overview page, and list page. Report generation is explicitly second stage per spec §12.
- Placeholder scan: no TODO/TBD placeholders remain.
- Type consistency: backend uses `brand_focus`, `sentiment`, `topics`, `post_type`, `risk_level`; frontend API uses the same names.
