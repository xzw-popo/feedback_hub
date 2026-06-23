import sqlite3

from feedback_hub.weibo.label_backfill import run_backfill
from feedback_hub.weibo.store import get_stats, init_schema, list_posts, upsert_post


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_run_backfill_writes_llm_label():
    conn = make_conn()
    upsert_post(
        conn,
        {"weibo_id": "4001", "url": "https://weibo.com/4001", "text": "豆包输入法 AI 好用", "raw": {}},
        keyword="豆包输入法",
    )

    def fake_classifier(post):
        assert post["weibo_id"] == "4001"
        return {
            "is_relevant": True,
            "brand_focus": "doubao",
            "sentiment": "positive",
            "topics": ["ai_capability", "input_experience"],
            "post_type": "review",
            "risk_level": "normal",
            "confidence": 0.91,
            "summary": "用户认可豆包输入法 AI。",
            "reason": "正文明确评价豆包输入法。",
            "label_source": "llm",
        }

    result = run_backfill(conn, classifier=fake_classifier, limit=10)

    assert result["labeled"] == 1
    item = list_posts(conn, limit=10, offset=0)["items"][0]
    assert item["label_source"] == "llm"
    assert item["topics"] == ["ai_capability", "input_experience"]
    assert "用户认可豆包输入法 AI" in item["reason"]


def test_run_backfill_hides_irrelevant_llm_results_by_default():
    conn = make_conn()
    upsert_post(
        conn,
        {"weibo_id": "4002", "url": "https://weibo.com/4002", "text": "猫踩键盘，在企业微信群里发乱码", "raw": {}},
        keyword="微信键盘",
        skip_irrelevant=False,
    )

    def fake_classifier(post):
        return {
            "is_relevant": False,
            "brand_focus": "other",
            "sentiment": "neutral",
            "topics": ["other"],
            "post_type": "other",
            "risk_level": "normal",
            "confidence": 0.88,
            "summary": "无关段子",
            "reason": "没有讨论输入法。",
            "label_source": "llm",
        }

    result = run_backfill(conn, classifier=fake_classifier, limit=10)

    assert result["hidden"] == 1
    assert list_posts(conn, limit=10, offset=0)["total"] == 0
    assert list_posts(conn, include_hidden=True, limit=10, offset=0)["total"] == 1
    assert get_stats(conn)["total_posts"] == 0
