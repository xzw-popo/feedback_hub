"""测试 LLM HTTP 客户端（用 monkeypatch 替换 requests.post）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from feedback_hub.tagger import llm_client


def _make_resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def test_classify_one_happy_path(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "run" in url:
            return _make_resp({"err_code": 0, "session_id": "s1", "chat_id": "c1"})
        return _make_resp({"err_code": 0, "content": "```json\n{}\n```", "has_more": False})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.classify_one("测试", retry=0)
    assert "json" in out
    assert any("run" in u for u in calls)
    assert any("poll" in u for u in calls)


def test_classify_one_run_error_raises(monkeypatch):
    def fake_post(url, **kw):
        return _make_resp({"err_code": 1, "err_msg": "no key"})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    with pytest.raises(llm_client.LLMClientError):
        llm_client.classify_one("测试", retry=0)


def test_classify_one_empty_session_id_raises(monkeypatch):
    def fake_post(url, **kw):
        return _make_resp({"err_code": 0, "session_id": "", "chat_id": ""})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    with pytest.raises(llm_client.LLMClientError):
        llm_client.classify_one("测试", retry=0)


def test_classify_one_retries_then_succeeds(monkeypatch):
    state = {"n": 0}

    def fake_post(url, **kw):
        if "run" in url:
            state["n"] += 1
            if state["n"] == 1:
                # 第一次失败
                return _make_resp({"err_code": 1, "err_msg": "transient"})
            return _make_resp({"err_code": 0, "session_id": "s", "chat_id": "c"})
        return _make_resp({"err_code": 0, "content": "{}", "has_more": False})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda *_: None)
    out = llm_client.classify_one("测试", retry=1)
    assert out == "{}"
    assert state["n"] == 2


def test_poll_handles_has_more_streaming(monkeypatch):
    poll_state = {"n": 0}

    def fake_post(url, **kw):
        if "run" in url:
            return _make_resp({"err_code": 0, "session_id": "s", "chat_id": "c"})
        poll_state["n"] += 1
        if poll_state["n"] < 3:
            return _make_resp({"err_code": 0, "content": f"part-{poll_state['n']}", "has_more": True, "interval": 10})
        return _make_resp({"err_code": 0, "content": "final", "has_more": False})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda *_: None)
    out = llm_client.classify_one("测试", retry=0)
    assert out == "final"
    assert poll_state["n"] == 3
