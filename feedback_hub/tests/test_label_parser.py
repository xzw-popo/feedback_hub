"""测试 LLM 回复 JSON 解析容错。"""
from __future__ import annotations

import json

from feedback_hub.tagger.label_parser import parse_llm_reply


def test_parses_clean_json_in_fence():
    reply = """```json
{"L1": "A.Bug", "L2": ["输入核心"], "severity": "P1", "confidence": 0.85, "reason": "卡顿"}
```"""
    r = parse_llm_reply(reply)
    assert r["L1"] == "A.Bug"
    assert r["L2"] == ["输入核心"]
    assert r["severity"] == "P1"
    assert r["confidence"] == 0.85
    assert r["source"] == "llm"


def test_parses_bare_json_without_fence():
    reply = '{"L1": "B.建议", "L2": [], "severity": "P3", "confidence": 0.7, "reason": "建议加皮肤"}'
    r = parse_llm_reply(reply)
    assert r["L1"] == "B.建议"
    assert r["severity"] == "P3"


def test_parses_json_with_text_around():
    reply = "好的，分类如下：\n```json\n{\"L1\":\"C.咨询\",\"L2\":[\"账号\"],\"severity\":\"P3\",\"confidence\":0.6,\"reason\":\"问登录\"}\n```\n请查收"
    r = parse_llm_reply(reply)
    assert r["L1"] == "C.咨询"
    assert r["L2"] == ["账号"]


def test_parses_generic_code_fence():
    reply = "```\n{\"L1\":\"D.情绪\",\"L2\":[],\"severity\":\"P3\",\"confidence\":0.5,\"reason\":\"赞\"}\n```"
    r = parse_llm_reply(reply)
    assert r["L1"] == "D.情绪"


def test_invalid_l1_falls_back_to_pending():
    reply = '{"L1": "Z.乱写", "L2": [], "severity": "P3", "confidence": 0.0, "reason": ""}'
    r = parse_llm_reply(reply)
    assert r["L1"] == "待定"


def test_invalid_l2_values_filtered_out():
    reply = '{"L1": "A.Bug", "L2": ["输入核心", "瞎编的"], "severity": "P1", "confidence": 0.8, "reason": "x"}'
    r = parse_llm_reply(reply)
    assert r["L2"] == ["输入核心"]


def test_l2_not_a_list_treated_as_empty():
    reply = '{"L1": "A.Bug", "L2": "输入核心", "severity": "P1", "confidence": 0.8, "reason": "x"}'
    r = parse_llm_reply(reply)
    assert r["L2"] == []


def test_invalid_severity_falls_back_to_p3():
    reply = '{"L1": "A.Bug", "L2": [], "severity": "P9", "confidence": 0.8, "reason": "x"}'
    r = parse_llm_reply(reply)
    assert r["severity"] == "P3"


def test_confidence_clamped_to_01():
    r = parse_llm_reply('{"L1":"A.Bug","L2":[],"severity":"P1","confidence":99,"reason":"x"}')
    assert r["confidence"] == 1.0
    r2 = parse_llm_reply('{"L1":"A.Bug","L2":[],"severity":"P1","confidence":-1,"reason":"x"}')
    assert r2["confidence"] == 0.0


def test_reason_is_truncated():
    long = "x" * 200
    r = parse_llm_reply(f'{{"L1":"A.Bug","L2":[],"severity":"P1","confidence":0.5,"reason":"{long}"}}')
    assert len(r["reason"]) <= 80


def test_no_json_returns_pending_with_parse_error_reason():
    r = parse_llm_reply("我无法分类这条消息")
    assert r["L1"] == "待定"
    assert "parse_error" in r["reason"]
    assert r["source"] == "llm"


def test_invalid_json_returns_pending():
    r = parse_llm_reply("```json\n{not valid json}\n```")
    assert r["L1"] == "待定"
    assert "parse_error" in r["reason"]


def test_empty_string_returns_pending():
    r = parse_llm_reply("")
    assert r["L1"] == "待定"


def test_returned_schema_complete():
    r = parse_llm_reply('{"L1":"A.Bug","L2":[],"severity":"P1","confidence":0.5,"reason":"x"}')
    for k in ("L1", "L2", "severity", "confidence", "reason", "source"):
        assert k in r
    assert r["source"] == "llm"
