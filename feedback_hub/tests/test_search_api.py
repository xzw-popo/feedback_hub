"""智能搜索 API 的元数据意图解析测试。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from feedback_hub import config, db
from feedback_hub.api import create_app
from feedback_hub.search.api import (
    _CANONICAL_PROBLEM_TERMS,
    MetadataFilters,
    _build_canonical_search_plan,
    _parse_structured_intent,
)
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
    _seed(
        conn,
        "fb7",
        "Windows 版本安装失败",
        "conv-210-win",
        base + 6000,
        appversion="2.1.0.20",
        platform="Win",
        device_name="ThinkPad X1",
    )
    _seed(
        conn,
        "fb8",
        "Android 语音输入时音乐播放异常",
        "conv-android-voice-music",
        base + 7000,
        appversion="4.0.0",
        platform="Android",
        device_name="Pixel 8",
    )
    _seed(
        conn,
        "fb9",
        "Android 语音输入很好用",
        "conv-android-voice-no-music",
        base + 8000,
        appversion="4.0.0",
        platform="Android",
        device_name="Pixel 8",
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


@pytest.mark.parametrize(
    ("query", "platform", "appversion"),
    [
        ("win2.1.0 ", "Win", "2.1.0"),
        ("iOS3.5.0", "iOS", "3.5.0"),
    ],
)
def test_structured_intent_extracts_compact_platform_version(query, platform, appversion):
    parsed = _parse_structured_intent(query, MetadataFilters())

    assert parsed.text_query == "反馈"
    assert parsed.filters.platform == platform
    assert parsed.filters.appversion == appversion


def test_structured_intent_extracts_android_abbreviation_with_duan():
    parsed = _parse_structured_intent("and 端语音输入音乐问题", MetadataFilters())

    assert parsed.text_query == "语音输入音乐问题"
    assert parsed.filters.platform == "Android"


@pytest.mark.parametrize(
    ("query", "text_query", "platform"),
    [
        ("android端语音输入音乐反馈", "语音输入音乐反馈", "Android"),
        ("mac 端剪贴板权限弹窗问题", "剪贴板权限弹窗问题", "Mac"),
        ("Windows 端键盘弹不出来", "键盘弹不出来", "Win"),
        ("iOS 端 3.5.0 语音问题", "语音问题", "iOS"),
    ],
)
def test_structured_intent_removes_platform_duan_suffix(query, text_query, platform):
    parsed = _parse_structured_intent(query, MetadataFilters())

    assert parsed.text_query == text_query
    assert parsed.filters.platform == platform


def test_smart_search_compact_platform_version_uses_metadata_only(search_client, monkeypatch):
    def fail_generate_search_intent(query: str):
        raise AssertionError("metadata-only search should not call structured intent LLM")

    def fail_generate_regex_patterns(query: str):
        raise AssertionError("metadata-only search should not call regex LLM")

    monkeypatch.setattr(
        "feedback_hub.search.api.generate_search_intent",
        fail_generate_search_intent,
    )
    monkeypatch.setattr(
        "feedback_hub.search.api.generate_regex_patterns",
        fail_generate_regex_patterns,
    )

    resp = search_client.post(
        "/api/smart-search",
        json={"query": "win2.1.0 ", "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert [item["conversation_id"] for item in data["items"]] == ["conv-210-win"]
    assert data["items"][0]["service_vid"] == config.DEFAULT_SERVICE_VID
    assert data["items"][0]["external_chat_url"] == (
        "https://wrfeedback.weread.woa.com/chat?"
        "channel=wetype&serviceVid=10000&userVid=u-conv-210-win"
    )
    assert data["debug"]["fallback"] == "metadata_only"
    assert data["debug"]["metadata_filters"]["platform"] == "Win"
    assert data["debug"]["metadata_filters"]["appversion"] == "2.1.0"


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
    assert data["total"] == 2
    assert {item["conversation_id"] for item in data["items"]} == {
        "conv-android-voice-music",
        "conv-voice-bad",
    }
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
            [
                "语音输入识别", "语音输入", "声音输入", "说话输入", "语音录入",
                "语音识别", "语音转文字", "语音键入", "声控", "语音",
            ],
            _CANONICAL_PROBLEM_TERMS,
        ],
        "exclude": [],
        "unknown_terms": [],
    }


def test_canonical_search_plan_keeps_uncovered_must_terms_required():
    plan = _build_canonical_search_plan(
        "语音输入音乐问题",
        {"must": [["语音输入"], ["音乐"], ["问题"]], "should": [], "exclude": []},
    )

    assert plan is not None
    assert plan["topics"] == ["voice_input_recognition"]
    assert ["音乐"] in plan["must"]
    assert plan["unknown_terms"] == ["音乐"]


@pytest.mark.parametrize(
    ("query", "intent_reply"),
    [
        (
            "帮我寻找一下android 端语音输入时音乐相关问题",
            {"must": [["语音输入"], ["音乐"], ["问题"]], "should": [], "exclude": []},
        ),
        (
            "帮我寻找一下and 端语音输入时音乐相关反馈",
            {"must": [["语音输入"], ["音乐"], ["反馈"]], "should": [], "exclude": []},
        ),
    ],
)
def test_smart_search_keeps_music_required_for_android_voice_queries(
    search_client,
    monkeypatch,
    query,
    intent_reply,
):
    def fake_generate_search_intent(text_query: str):
        return intent_reply

    def fail_generate_regex_patterns(text_query: str):
        raise AssertionError("canonical music search should not run regex generation")

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
        json={"query": query, "filters": {}, "limit": 20, "offset": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert [item["conversation_id"] for item in data["items"]] == ["conv-android-voice-music"]
    assert data["debug"]["metadata_filters"]["platform"] == "Android"
    assert ["音乐"] in data["debug"]["search_plan"]["must"]
    assert data["debug"]["search_plan"]["unknown_terms"] == ["音乐"]


@pytest.mark.parametrize(
    ("query", "intent_terms", "topic_id", "expected_terms"),
    [
        (
            "候选词不准",
            {"must": [["候选词"], ["不准"]], "should": [], "exclude": []},
            "candidate_words",
            ["候选词", "联想词", "预测词", "候选栏", "候选"],
        ),
        (
            "键盘不弹出来",
            {"must": [["键盘不弹"], ["没反应"]], "should": [], "exclude": []},
            "keyboard_popup",
            ["键盘弹出", "键盘不弹", "唤起键盘", "调起键盘", "弹不出键盘"],
        ),
        (
            "输入法闪退",
            {"must": [["闪退"], ["问题"]], "should": [], "exclude": []},
            "crash",
            ["闪退", "崩溃", "异常退出", "自动关闭", "退出"],
        ),
        (
            "皮肤显示异常",
            {"must": [["皮肤"], ["异常"]], "should": [], "exclude": []},
            "keyboard_skin",
            ["皮肤", "主题", "键盘皮肤", "键盘主题"],
        ),
        (
            "词库同步失败",
            {"must": [["词库同步"], ["失败"]], "should": [], "exclude": []},
            "sync_settings",
            ["同步", "云同步", "配置同步", "词库同步"],
        ),
        (
            "升级失败",
            {"must": [["升级"], ["失败"]], "should": [], "exclude": []},
            "install_update",
            ["安装", "更新", "升级", "版本更新"],
        ),
    ],
)
def test_canonical_search_plan_covers_common_topics(
    query,
    intent_terms,
    topic_id,
    expected_terms,
):
    plan = _build_canonical_search_plan(query, intent_terms)

    assert plan is not None
    assert plan["mode"] == "canonical"
    assert topic_id in plan["topics"]
    assert plan["intent"] == "problem"
    assert expected_terms in plan["must"]
    assert _CANONICAL_PROBLEM_TERMS in plan["must"]


def test_canonical_search_plan_does_not_keep_extra_problem_synonyms_as_unknown_required_terms():
    plan = _build_canonical_search_plan(
        "语音输入音乐相关问题",
        {
            "must": [["语音输入"], ["音乐"], ["问题", "中断", "太多"]],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert ["音乐"] in plan["must"]
    assert ["中断", "太多"] not in plan["must"]
    assert plan["unknown_terms"] == ["音乐"]


def test_canonical_search_plan_does_not_match_cursor_topic_from_candidate_words():
    plan = _build_canonical_search_plan(
        "候选词不准",
        {"must": [["候选词"], ["不准"]], "should": [], "exclude": []},
    )

    assert plan is not None
    assert "candidate_words" in plan["topics"]
    assert "cursor_selection" not in plan["topics"]


def test_canonical_search_plan_does_not_cascade_permission_popup_into_unrelated_topics():
    plan = _build_canonical_search_plan(
        "剪贴板权限弹窗问题",
        {
            "must": [
                ["剪贴板", "复制"],
                ["权限", "授权", "麦克风", "录音"],
                ["弹窗", "广告", "推广", "活动弹窗"],
                ["问题"],
            ],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert "clipboard_input" in plan["topics"]
    assert "privacy_permission" in plan["topics"]
    assert "ads_promotion" not in plan["topics"]
    assert "activity_reward" not in plan["topics"]
    assert "voice_permission" not in plan["topics"]


def test_canonical_search_plan_drops_llm_feedback_noise_from_unknown_required_terms():
    plan = _build_canonical_search_plan(
        "语音输入音乐相关反馈",
        {
            "must": [
                ["语音输入", "语音识别"],
                ["音乐", "音频", "歌曲"],
                ["反馈", "问题", "bug", "体验", "意见", "评价", "回馈", "响应"],
            ],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert ["音乐", "音频", "歌曲"] in plan["must"]
    assert ["bug", "体验", "评价", "回馈", "响应"] not in plan["must"]
    assert plan["unknown_terms"] == ["音乐", "音频", "歌曲"]


def test_canonical_search_plan_drops_llm_platform_side_noise_from_unknown_required_terms():
    plan = _build_canonical_search_plan(
        "剪贴板权限弹窗问题",
        {
            "must": [
                ["剪贴板", "clipboard"],
                ["权限", "permission"],
                ["弹窗", "popup", "dialog"],
                ["端", "客户端", "终端"],
            ],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert ["端", "客户端", "终端"] not in plan["must"]
    assert "端" not in plan["unknown_terms"]


def test_canonical_search_plan_treats_llm_voice_synonyms_as_topic_covered():
    plan = _build_canonical_search_plan(
        "语音输入音乐相关反馈",
        {
            "must": [["语音输入", "声控", "语音键入", "声音输入", "说话输入", "语音录入"], ["音乐"]],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert ["声控", "语音键入", "声音输入", "说话输入", "语音录入"] not in plan["must"]
    assert plan["unknown_terms"] == ["音乐"]


def test_canonical_search_plan_drops_llm_comment_as_feedback_noise():
    plan = _build_canonical_search_plan(
        "语音输入音乐相关反馈",
        {
            "must": [["语音输入"], ["音乐"], ["反馈", "评论"]],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert ["评论"] not in plan["must"]
    assert plan["unknown_terms"] == ["音乐"]


def test_canonical_search_plan_treats_llm_clipboard_popup_synonyms_as_covered_or_generic():
    plan = _build_canonical_search_plan(
        "剪贴板权限弹窗问题",
        {
            "must": [
                ["剪贴板", "剪切板", "粘贴板"],
                ["权限"],
                ["弹窗", "弹出", "弹出窗口", "提示框", "对话框"],
            ],
            "should": [],
            "exclude": [],
        },
    )

    assert plan is not None
    assert "剪切板" not in plan["unknown_terms"]
    assert "弹出窗口" not in plan["unknown_terms"]
