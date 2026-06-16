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
