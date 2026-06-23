import sqlite3

from feedback_hub.weibo.store import (
    get_stats,
    init_schema,
    list_posts,
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
    assert posts["items"][0]["sentiment"] == "positive"


def test_repeated_upsert_updates_post_and_preserves_hits():
    conn = make_conn()

    upsert_post(conn, {"weibo_id": "1002", "url": "https://weibo.com/1002", "text": "豆包输入法 AI 好用", "raw": {}}, keyword="豆包输入法", searched_at=100)
    result = upsert_post(conn, {"weibo_id": "1002", "url": "https://weibo.com/1002", "text": "豆包输入法 AI 好用 推荐", "raw": {}}, keyword="豆包", searched_at=200)

    assert result["inserted"] is False
    posts = list_posts(conn, q="推荐", limit=20, offset=0)
    assert posts["total"] == 1
    assert posts["items"][0]["keywords"] == ["豆包输入法", "豆包"]


def test_stats_counts_brand_sentiment_and_latest_run():
    conn = make_conn()

    upsert_post(conn, {"weibo_id": "1003", "url": "https://weibo.com/1003", "text": "豆包输入法 AI 好用", "raw": {}}, keyword="豆包输入法")
    stats = get_stats(conn)

    assert stats["total_posts"] == 1
    assert stats["brand_focus_counts"]["doubao"] == 1
    assert stats["sentiment_counts"]["positive"] == 1
    assert stats["topic_counts"]["ai_capability"] == 1
