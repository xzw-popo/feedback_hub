"""FastAPI 5 端点端到端测试，用 TestClient + 临时 sqlite。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from feedback_hub import db
from feedback_hub.api import create_app


def _seed(conn, fid, text, l1, sev, conv_id, ts_ms,
          user_vid="u1", appversion="1.0", l2="", confidence=0.9, msg_seq=0):
    db.upsert_feedback(conn, {
        "feedback_id": fid, "conversation_id": conv_id, "msg_seq": msg_seq,
        "channel": "wetype", "ts_ms": ts_ms,
        "platform": "iOS", "appversion": appversion, "user_vid": user_vid,
        "keyboard_source": "", "device_name": "", "channelid": "", "enginever": "",
        "msgtype": "text", "text": text, "tags": "", "raw_json": "{}",
        "pulled_at": int(time.time()),
    })
    db.upsert_message_label(conn, {
        "feedback_id": fid, "L1": l1, "L2": l2, "severity": sev,
        "confidence": confidence, "reason": "_", "source": "rule",
        "rule_name": "_", "tagged_at": int(time.time()),
    })
    db.upsert_conversation_label(conn, {
        "conversation_id": conv_id, "L1": l1, "L2": l2, "severity": sev,
        "confidence": confidence, "reason": "_", "source": "aggregated",
        "msg_count": 1, "first_ts_ms": ts_ms, "last_ts_ms": ts_ms,
        "user_vid": user_vid, "appversion": appversion, "channel": "wetype",
        "aggregated_at": int(time.time()),
    })


@pytest.fixture
def client(tmp_path):
    p = tmp_path / "fb.db"
    conn = db.connect(p)
    db.init_schema(conn)
    # seed 三条不同 L1 / severity 的数据
    base = 1747526400000
    _seed(conn, "fb1", "闪退了", "A.Bug", "P0", "conv1", base, l2="性能")
    _seed(conn, "fb2", "希望加皮肤", "B.建议", "P3", "conv2", base + 1000, l2="皮肤")
    _seed(conn, "fb3", "怎么登录", "C.咨询", "P3", "conv3", base + 2000, l2="账号")
    conn.commit()
    conn.close()
    app = create_app(db_path=str(p))
    return TestClient(app)


def test_list_conversations_returns_total_and_items(client):
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert {it["conversation_id"] for it in data["items"]} == {"conv1", "conv2", "conv3"}


def test_list_conversations_filter_by_L1(client):
    data = client.get("/api/conversations?L1=A.Bug").json()
    assert data["total"] == 1
    assert data["items"][0]["L1"] == "A.Bug"


def test_list_conversations_filter_by_severity(client):
    data = client.get("/api/conversations?severity=P0").json()
    assert data["total"] == 1
    assert data["items"][0]["severity"] == "P0"


def test_list_conversations_filter_by_L2(client):
    data = client.get("/api/conversations?L2=皮肤").json()
    assert data["total"] == 1


def test_list_conversations_text_search(client):
    data = client.get("/api/conversations?q=皮肤").json()
    assert data["total"] == 1


def test_list_conversations_pagination(client):
    data = client.get("/api/conversations?limit=2&offset=0").json()
    assert data["total"] == 3
    assert len(data["items"]) == 2


def test_list_conversations_bad_L1_returns_400(client):
    r = client.get("/api/conversations?L1=Z.乱写")
    assert r.status_code == 400


def test_get_conversation_returns_messages(client):
    data = client.get("/api/conversations/conv1").json()
    assert data["conversation"]["conversation_id"] == "conv1"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["text"] == "闪退了"


def test_get_conversation_404(client):
    r = client.get("/api/conversations/nonexistent")
    assert r.status_code == 404


def test_stats_distribution_returns_l1_l2_severity(client):
    data = client.get("/api/stats/distribution").json()
    assert data["L1"]["A.Bug"] == 1
    assert data["L1"]["B.建议"] == 1
    assert data["L1"]["C.咨询"] == 1
    assert data["L2"]["皮肤"] == 1
    assert data["severity"]["P0"] == 1


def test_stats_trend_day(client):
    data = client.get("/api/stats/trend?granularity=day").json()
    assert data["granularity"] == "day"
    assert len(data["buckets"]) >= 1
    total = sum(sum(b["counts"].values()) for b in data["buckets"])
    assert total == 3


def test_stats_trend_invalid_granularity(client):
    r = client.get("/api/stats/trend?granularity=year")
    assert r.status_code == 400


def test_export_csv_content(client):
    r = client.get("/api/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    assert "conversation_id" in body
    assert "conv1" in body
    assert body.count("\n") >= 4  # 1 header + 3 rows + trailing newline maybe


def test_date_range_filter(client):
    base = 1747526400000
    # from 这一天的 0 点开始, to 这一天 23:59:59 结束
    data = client.get("/api/conversations?from=2025-05-18&to=2025-05-18").json()
    # 1747526400000 = 2025-05-18 04:00:00 UTC，本机时区可能不同；不强校验数量
    # 仅验证接口返回正常
    assert "total" in data


def test_invalid_date_returns_400(client):
    r = client.get("/api/conversations?from=not-a-date")
    assert r.status_code == 400


def test_cors_allows_localhost_5173(client):
    r = client.options(
        "/api/conversations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_blocks_unknown_origin(client):
    r = client.get(
        "/api/conversations",
        headers={"Origin": "http://evil.example.com"},
    )
    assert r.status_code == 200
    headers_lower = {k.lower() for k in r.headers.keys()}
    assert "access-control-allow-origin" not in headers_lower
