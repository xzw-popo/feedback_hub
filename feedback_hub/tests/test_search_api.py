"""智能搜索 API 的元数据意图解析测试。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from feedback_hub import config, db
from feedback_hub.api import create_app
from feedback_hub.search.llm_client import SearchLLMError


def _seed(
    conn,
    fid: str,
    text: str,
    conv_id: str,
    ts_ms: int,
    *,
    appversion: str,
    platform: str,
    device_name: str = "",
) -> None:
    now = int(time.time())
    db.upsert_feedback(conn, {
        "feedback_id": fid,
        "conversation_id": conv_id,
        "msg_seq": 0,
        "channel": "wetype",
        "ts_ms": ts_ms,
        "platform": platform,
        "appversion": appversion,
        "user_vid": f"u-{conv_id}",
        "keyboard_source": "",
        "device_name": device_name,
        "channelid": "",
        "enginever": "",
        "msgtype": "text",
        "text": text,
        "tags": "",
        "raw_json": "{}",
        "pulled_at": now,
    })
    db.upsert_message_label(conn, {
        "feedback_id": fid,
        "L1": "A.Bug",
        "L2": "性能",
        "severity": "P0",
        "confidence": 0.9,
        "reason": "_",
        "source": "rule",
        "rule_name": "_",
        "tagged_at": now,
    })
    db.upsert_conversation_label(conn, {
        "conversation_id": conv_id,
        "L1": "A.Bug",
        "L2": "性能",
        "severity": "P0",
        "confidence": 0.9,
        "reason": "_",
        "source": "aggregated",
        "msg_count": 1,
        "first_ts_ms": ts_ms,
        "last_ts_ms": ts_ms,
        "user_vid": f"u-{conv_id}",
        "appversion": appversion,
        "channel": "wetype",
        "aggregated_at": now,
    })


@pytest.fixture
def search_client(tmp_path, monkeypatch):
    db_path = tmp_path / "fb.db"
    conn = db.connect(db_path)
    db.init_schema(conn)
    base = 1747526400000
    _seed(
        conn,
        "fb1",
        "打开输入法就闪退",
        "conv-350-mac",
        base,
        appversion="3.5.0",
        platform="Mac",
        device_name="MacBookPro18,3",
    )
    _seed(
        conn,
        "fb2",
        "打开输入法就闪退",
        "conv-360-mac",
        base + 1000,
        appversion="3.6.0",
        platform="Mac",
        device_name="MacBookPro18,3",
    )
    _seed(
        conn,
        "fb3",
        "打开输入法就闪退",
        "conv-350-win",
        base + 2000,
        appversion="3.5.0",
        platform="Win",
        device_name="ThinkPad X1",
    )
    _seed(
        conn,
        "fb4",
        "切换键盘时闪退",
        "conv-iphone15",
        base + 3000,
        appversion="3.5.0",
        platform="iOS",
        device_name="iPhone15,2",
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", db_path)
    return TestClient(create_app())


def test_smart_search_extracts_version_and_platform_from_query(search_client, monkeypatch):
    seen_queries: list[str] = []

    def fake_generate_regex_patterns(query: str):
        seen_queries.append(query)
        return [["闪退"]], ["OR"], "OR"

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_regex_patterns",
        fake_generate_regex_patterns,
    )

    resp = search_client.post(
        "/api/smart-search",
        json={"query": "查看 3.5.0 mac 闪退", "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert seen_queries == ["查看 闪退"]
    assert data["total"] == 1
    assert [item["conversation_id"] for item in data["items"]] == ["conv-350-mac"]


def test_smart_search_extracts_device_name_without_making_it_text_condition(search_client, monkeypatch):
    seen_queries: list[str] = []

    def fake_generate_regex_patterns(query: str):
        seen_queries.append(query)
        return [["闪退"]], ["OR"], "OR"

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_regex_patterns",
        fake_generate_regex_patterns,
    )

    resp = search_client.post(
        "/api/smart-search",
        json={"query": "iPhone15 闪退", "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert seen_queries == ["闪退"]
    assert data["total"] == 1
    assert [item["conversation_id"] for item in data["items"]] == ["conv-iphone15"]


def test_smart_search_falls_back_to_query_keywords_when_llm_patterns_invalid(search_client, monkeypatch):
    def fake_generate_regex_patterns(query: str):
        raise SearchLLMError("LLM generated no valid regex patterns")

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_regex_patterns",
        fake_generate_regex_patterns,
    )

    resp = search_client.post(
        "/api/smart-search",
        json={"query": "闪退", "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert data["debug"]["fallback"] == "query_keywords"
