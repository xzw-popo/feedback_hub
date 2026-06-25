import sqlite3

from feedback_hub.weibo.store import (
    apply_label,
    get_stats,
    init_schema,
    list_posts,
    parse_weibo_created_at,
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


def test_parse_weibo_created_at_freezes_relative_today_values():
    base = 1782216800

    assert parse_weibo_created_at("今天16:00", base) == 1782201600000
    assert parse_weibo_created_at("20分钟前", base) == (base - 20 * 60) * 1000
    assert parse_weibo_created_at("06月22日 21:49", base) == 1782136140000
    assert parse_weibo_created_at("2025年11月03日 16:11", base) == 1762157460000


def test_upsert_parses_created_at_raw_to_absolute_timestamp():
    conn = make_conn()

    upsert_post(
        conn,
        {
            "weibo_id": "1010",
            "url": "https://weibo.com/1010",
            "created_at_raw": "今天16:00",
            "text": "微信输入法的语音输入功能也太舒服了",
            "raw": {},
        },
        keyword="微信输入法",
        searched_at=1782216800,
    )

    posts = list_posts(conn, limit=20, offset=0)

    assert posts["items"][0]["created_at_ms"] == 1782201600000


def test_repeated_upsert_updates_post_and_preserves_hits():
    conn = make_conn()

    upsert_post(conn, {"weibo_id": "1002", "url": "https://weibo.com/1002", "text": "豆包输入法 AI 好用", "raw": {}}, keyword="豆包输入法", searched_at=100)
    result = upsert_post(conn, {"weibo_id": "1002", "url": "https://weibo.com/1002", "text": "豆包输入法 AI 好用 推荐", "raw": {}}, keyword="豆包", searched_at=200)

    assert result["inserted"] is False
    posts = list_posts(conn, q="推荐", limit=20, offset=0)
    assert posts["total"] == 1
    assert posts["items"][0]["keywords"] == ["豆包输入法", "豆包"]


def test_repeated_upsert_does_not_overwrite_existing_llm_label():
    conn = make_conn()

    upsert_post(
        conn,
        {"weibo_id": "1011", "url": "https://weibo.com/1011", "text": "豆包输入法 AI 好用", "raw": {}},
        keyword="豆包输入法",
        searched_at=100,
    )
    apply_label(
        conn,
        "1011",
        {
            "is_relevant": True,
            "brand_focus": "doubao",
            "sentiment": "positive",
            "topics": ["ai_capability"],
            "post_type": "review",
            "risk_level": "normal",
            "confidence": 0.91,
            "summary": "LLM summary",
            "reason": "LLM reason",
            "label_source": "llm",
        },
        labeled_at=150,
    )
    conn.commit()

    upsert_post(
        conn,
        {"weibo_id": "1011", "url": "https://weibo.com/1011", "text": "豆包输入法 AI 好用 推荐", "raw": {}},
        keyword="豆包输入法",
        searched_at=200,
    )

    item = list_posts(conn, limit=20, offset=0)["items"][0]
    assert item["label_source"] == "llm"
    assert item["reason"] == "LLM summary\n\nLLM reason"


def test_stats_counts_brand_sentiment_and_latest_run():
    conn = make_conn()

    upsert_post(conn, {"weibo_id": "1003", "url": "https://weibo.com/1003", "text": "豆包输入法 AI 好用", "raw": {}}, keyword="豆包输入法")
    stats = get_stats(conn)

    assert stats["total_posts"] == 1
    assert stats["brand_focus_counts"]["doubao"] == 1
    assert stats["sentiment_counts"]["positive"] == 1
    assert stats["topic_counts"]["ai_capability"] == 1


def test_date_filters_fall_back_to_last_seen_when_created_at_is_missing():
    conn = make_conn()

    upsert_post(
        conn,
        {
            "weibo_id": "1004",
            "url": "https://weibo.com/1004",
            "created_at_raw": "今天18:31",
            "text": "微信键盘挺聪明",
            "raw": {},
        },
        keyword="微信键盘",
        searched_at=1782216800,
    )

    stats = get_stats(conn, from_="2026-06-23", to="2026-06-23")
    posts = list_posts(conn, from_="2026-06-23", to="2026-06-23", limit=20, offset=0)

    assert stats["total_posts"] == 1
    assert posts["total"] == 1


def test_upsert_skips_irrelevant_weibo_search_noise():
    conn = make_conn()

    result = upsert_post(
        conn,
        {
            "weibo_id": "1005",
            "url": "https://weibo.com/1005",
            "text": "更好看的来咯来咯",
            "raw": {},
        },
        keyword="微信键盘 豆包",
        searched_at=1782216800,
    )

    assert result["inserted"] is False
    assert result["skipped"] is True
    assert list_posts(conn, limit=20, offset=0)["total"] == 0


def test_upsert_skips_generic_wechat_and_keyboard_mentions():
    conn = make_conn()

    result = upsert_post(
        conn,
        {
            "weibo_id": "1007",
            "url": "https://weibo.com/1007",
            "text": "猫咪踩键盘，在企业微信工作群里发了半小时乱码",
            "raw": {},
        },
        keyword="微信键盘",
        searched_at=1782216800,
    )

    assert result["inserted"] is False
    assert result["skipped"] is True
    assert list_posts(conn, limit=20, offset=0)["total"] == 0


def test_upsert_keeps_exact_wechat_input_method_mentions():
    conn = make_conn()

    result = upsert_post(
        conn,
        {
            "weibo_id": "1008",
            "url": "https://weibo.com/1008",
            "text": "微信输入法的语音输入功能也太舒服了",
            "raw": {},
        },
        keyword="微信输入法",
        searched_at=1782216800,
    )

    assert result["inserted"] is True
    assert list_posts(conn, brand_focus="wechat", limit=20, offset=0)["total"] == 1


def test_upsert_keeps_doubao_input_related_variant():
    conn = make_conn()

    result = upsert_post(
        conn,
        {
            "weibo_id": "1006",
            "url": "https://weibo.com/1006",
            "text": "豆包语音输入法真的省事，长微博都能直接说出来",
            "raw": {},
        },
        keyword="豆包输入法",
        searched_at=1782216800,
    )

    assert result["inserted"] is True
    assert list_posts(conn, brand_focus="doubao", limit=20, offset=0)["total"] == 1
