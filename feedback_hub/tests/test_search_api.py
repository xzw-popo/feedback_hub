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
    _seed(
        conn,
        "fb5",
        "语音输入用不了，点了没有反应",
        "conv-voice-bad",
        base + 4000,
        appversion="3.5.0",
        platform="iOS",
        device_name="iPhone15,2",
    )
    _seed(
        conn,
        "fb6",
        "语音输入很好用",
        "conv-voice-good",
        base + 5000,
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

    def fake_generate_search_intent(query: str):
        raise SearchLLMError("structured intent unavailable")

    def fake_generate_regex_patterns(query: str):
        seen_queries.append(query)
        return [["闪退"]], ["OR"], "OR"

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fake_generate_search_intent,
    )
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

    def fake_generate_search_intent(query: str):
        raise SearchLLMError("structured intent unavailable")

    def fake_generate_regex_patterns(query: str):
        seen_queries.append(query)
        return [["闪退"]], ["OR"], "OR"

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fake_generate_search_intent,
    )
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
    def fake_generate_search_intent(query: str):
        raise SearchLLMError("structured intent unavailable")

    def fake_generate_regex_patterns(query: str):
        raise SearchLLMError("LLM generated no valid regex patterns")

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fake_generate_search_intent,
    )
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


def test_smart_search_uses_structured_intent_terms_before_regex(search_client, monkeypatch):
    seen_queries: list[str] = []

    def fake_generate_search_intent(query: str):
        seen_queries.append(query)
        return {
            "must": [["语音输入", "听写"], ["不好用", "用不了", "没反应"]],
            "should": [],
            "exclude": [],
        }

    def fail_generate_regex_patterns(query: str):
        raise AssertionError("regex generation should not run when structured intent works")

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fake_generate_search_intent,
    )
    monkeypatch.setattr(
        "feedback_hub.search.api.generate_regex_patterns",
        fail_generate_regex_patterns,
    )

    resp = search_client.post(
        "/api/smart-search",
        json={"query": "语音输入不好用", "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert seen_queries == ["语音输入不好用"]
    assert data["total"] == 1
    assert [item["conversation_id"] for item in data["items"]] == ["conv-voice-bad"]
    assert data["debug"]["fallback"] == "structured_intent"
    assert data["debug"]["intent_terms"]["must"] == [
        ["语音输入", "听写"],
        ["不好用", "用不了", "没反应"],
    ]


def test_smart_search_ignores_generic_complaint_terms_in_intent(search_client, monkeypatch):
    def fake_generate_search_intent(query: str):
        return {
            "must": [["语音"], ["投诉", "申诉", "举报"]],
            "should": [],
            "exclude": [],
        }

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fake_generate_search_intent,
    )

    resp = search_client.post(
        "/api/smart-search",
        json={"query": "语音在 3.5.0 的投诉", "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert [item["conversation_id"] for item in data["items"]] == ["conv-voice-bad"]
    assert data["debug"]["intent_terms"]["must"] == [
        ["语音"],
        ["不好", "不能", "无法", "用不了", "没反应", "不对", "问题"],
    ]


def test_smart_search_canonicalizes_voice_problem_plan(search_client, monkeypatch):
    intent_replies = [
        {
            "must": [["语音"], ["问题"]],
            "should": [],
            "exclude": [],
        },
        {
            "must": [["语音输入识别"], ["故障"]],
            "should": [],
            "exclude": [],
        },
    ]

    def fake_generate_search_intent(query: str):
        return intent_replies.pop(0)

    def fail_generate_regex_patterns(query: str):
        raise AssertionError("regex generation should not run for canonical plans")

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fake_generate_search_intent,
    )
    monkeypatch.setattr(
        "feedback_hub.search.api.generate_regex_patterns",
        fail_generate_regex_patterns,
    )

    payload = {"query": "语音在 3.5.0 的问题", "filters": {}, "limit": 20, "offset": 0}
    first = search_client.post("/api/smart-search", json=payload)
    second = search_client.post("/api/smart-search", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()

    assert first_data["total"] == second_data["total"] == 1
    assert [item["conversation_id"] for item in first_data["items"]] == ["conv-voice-bad"]
    assert [item["conversation_id"] for item in second_data["items"]] == ["conv-voice-bad"]
    assert first_data["debug"]["search_plan"] == second_data["debug"]["search_plan"]
    assert first_data["debug"]["search_plan"] == {
        "mode": "canonical",
        "topics": ["voice_input_recognition"],
        "intent": "problem",
        "must": [
            ["语音输入识别", "语音输入", "语音识别", "语音转文字", "语音"],
            ["不好", "不能", "无法", "用不了", "没反应", "不对", "问题", "异常", "故障", "失灵", "不准", "识别不了"],
        ],
        "exclude": [],
        "unknown_terms": [],
    }
