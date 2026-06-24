from __future__ import annotations

from feedback_hub.search import llm_client


def test_generate_search_intent_is_deterministic_and_cached(monkeypatch):
    calls: list[float] = []

    def fake_chat_completion(messages, *, temperature=0.1, max_tokens=16384, timeout=60):
        calls.append(temperature)
        return '{"must": [["语音"], ["问题"]], "should": [], "exclude": []}'

    if hasattr(llm_client.generate_search_intent, "cache_clear"):
        llm_client.generate_search_intent.cache_clear()
    monkeypatch.setattr(llm_client, "chat_completion", fake_chat_completion)

    first = llm_client.generate_search_intent("语音在 的问题")
    second = llm_client.generate_search_intent("语音在 的问题")

    assert first == second == {
        "must": [["语音"], ["问题"]],
        "should": [],
        "exclude": [],
    }
    assert calls == [0]
