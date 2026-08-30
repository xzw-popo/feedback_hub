import sqlite3

from feedback_hub.weibo.store import init_schema, list_posts
from feedback_hub.weibo.sync import run_sync


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_run_sync_ingests_labels_and_records_crawl_run():
    conn = make_conn()

    def fake_crawler(*, keyword, pages, cookie, start, end, sort):
        assert cookie == "cookie-value"
        assert start == "2026-06-24 00"
        assert end == "2026-06-24 23"
        assert sort == "time"
        return [
            {
                "weibo_id": f"{keyword}-1",
                "url": f"https://weibo.com/{keyword}-1",
                "created_at_raw": "2026-06-24 10:00",
                "text": f"{keyword} 豆包输入法和微信输入法对比",
                "raw": {},
            }
        ]

    def fake_labeler(post):
        return {
            "is_relevant": True,
            "brand_focus": "comparison",
            "sentiment": "mixed",
            "topics": ["feature_comparison"],
            "post_type": "review",
            "risk_level": "normal",
            "confidence": 0.8,
            "summary": "对比讨论",
            "reason": post["weibo_id"],
            "label_source": "llm",
        }

    result = run_sync(
        conn,
        queries=[{"name": "compare", "keyword": "微信输入法 豆包", "pages": 1}],
        cookie="cookie-value",
        start="2026-06-24 00",
        end="2026-06-24 23",
        crawler=fake_crawler,
        labeler=fake_labeler,
    )

    posts = list_posts(conn, limit=20, offset=0)
    run = conn.execute("SELECT * FROM weibo_crawl_run").fetchone()

    assert result["total_seen"] == 1
    assert result["inserted_posts"] == 1
    assert result["label_result"]["labeled"] == 1
    assert posts["items"][0]["label_source"] == "llm"
    assert run["status"] == "succeeded"
    assert run["total_seen"] == 1
    assert run["inserted_posts"] == 1
