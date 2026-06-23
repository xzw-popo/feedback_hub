from fastapi.testclient import TestClient

from feedback_hub import db
from feedback_hub.api import create_app
from feedback_hub.weibo.store import init_schema, upsert_post


def test_weibo_posts_api_filters_by_brand(tmp_path):
    db_path = tmp_path / "feedback.db"
    conn = db.connect(db_path)
    init_schema(conn)
    upsert_post(
        conn,
        {"weibo_id": "2001", "url": "https://weibo.com/2001", "text": "豆包输入法 AI 好用", "raw": {}},
        keyword="豆包输入法",
    )
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
    upsert_post(
        conn,
        {"weibo_id": "2002", "url": "https://weibo.com/2002", "text": "微信键盘广告太烦", "raw": {}},
        keyword="微信键盘",
    )
    conn.close()

    client = TestClient(create_app(db_path=str(db_path)))
    resp = client.get("/api/weibo/stats")

    assert resp.status_code == 200
    assert resp.json()["brand_focus_counts"]["wechat"] == 1


def test_weibo_post_detail_api_returns_raw_payload(tmp_path):
    db_path = tmp_path / "feedback.db"
    conn = db.connect(db_path)
    init_schema(conn)
    upsert_post(
        conn,
        {
            "weibo_id": "2003",
            "url": "https://weibo.com/2003",
            "text": "微信输入法和豆包对比",
            "raw": {"mid": "2003"},
        },
        keyword="微信输入法 豆包",
    )
    conn.close()

    client = TestClient(create_app(db_path=str(db_path)))
    resp = client.get("/api/weibo/posts/2003")

    assert resp.status_code == 200
    assert resp.json()["raw"]["raw"]["mid"] == "2003"
