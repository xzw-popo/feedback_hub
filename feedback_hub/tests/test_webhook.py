"""测试 pusher.webhook：POST + 重试 + 失败语义。"""
from __future__ import annotations

import pytest

from feedback_hub.pusher import webhook


class _FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {"errcode": 0, "errmsg": "ok"}

    def json(self):
        return self._body


def test_send_success_no_retry(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _FakeResp(200)

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=3,
    )
    assert ok is True
    assert len(calls) == 1


def test_send_retries_then_succeeds(monkeypatch):
    counter = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        counter["n"] += 1
        if counter["n"] < 3:
            raise webhook.requests.RequestException("network")
        return _FakeResp(200)

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=3,
    )
    assert ok is True
    assert counter["n"] == 3


def test_send_all_retries_fail(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise webhook.requests.RequestException("boom")

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=3,
    )
    assert ok is False


def test_send_returns_false_on_errcode_non_zero(monkeypatch):
    """企微返回 200 但 errcode != 0 也算失败。"""
    def fake_post(url, json=None, timeout=None):
        return _FakeResp(200, body={"errcode": 93000, "errmsg": "invalid"})

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    ok = webhook.send_markdown(
        webhook_url="https://x", markdown="# hi", max_retries=2,
    )
    assert ok is False


def test_send_payload_shape(monkeypatch):
    payloads = []

    def fake_post(url, json=None, timeout=None):
        payloads.append(json)
        return _FakeResp(200)

    monkeypatch.setattr("feedback_hub.pusher.webhook.requests.post", fake_post)
    monkeypatch.setattr("feedback_hub.pusher.webhook.time.sleep",
                        lambda s: None)

    webhook.send_markdown(webhook_url="https://x", markdown="# hi")
    assert payloads[0]["msgtype"] == "markdown"
    assert payloads[0]["markdown"]["content"] == "# hi"
