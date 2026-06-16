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
