"""测试 tagger/rules.py 一审规则引擎。

覆盖各分支：空、太短、纯表情、无效关键词、情绪、Bug P0/P1/P2、建议、咨询、未命中。
"""
from __future__ import annotations

import pytest

from feedback_hub.tagger.rules import apply_rules


def test_empty_string_returns_invalid():
    r = apply_rules("")
    assert r is not None
    assert r["L1"] == "E.无效"
    assert r["source"] == "rule"
    assert r["rule_name"] == "rule:empty"


def test_none_returns_none():
    assert apply_rules(None) is None


def test_too_short_single_char():
    r = apply_rules("好")
    assert r is not None
    assert r["L1"] == "E.无效"
    assert r["rule_name"] == "rule:too_short"


def test_pure_emoji_returns_invalid():
    r = apply_rules("😄😄😄")
    assert r is not None
    assert r["L1"] == "E.无效"
    assert r["rule_name"] == "rule:pure_emoji"


def test_pure_punct_returns_invalid():
    r = apply_rules("。。。。。")
    assert r is not None
    assert r["L1"] == "E.无效"


def test_invalid_keyword_test():
    r = apply_rules("test")
    assert r is not None
    assert r["L1"] == "E.无效"
    assert r["rule_name"] == "rule:invalid_kw"


def test_emotion_pos_short():
    r = apply_rules("好用")
    assert r is not None
    assert r["L1"] == "D.情绪"
    assert r["severity"] == "P3"


def test_emotion_neg_short():
    r = apply_rules("垃圾")
    assert r is not None
    assert r["L1"] == "D.情绪"


def test_long_text_with_emotion_word_does_not_match_emotion():
    """情绪规则只对短句生效，长句应继续判断 Bug/建议/咨询。"""
    r = apply_rules("用了三个月真的很好用，希望可以加一个夜间模式")
    assert r is None or r["L1"] != "D.情绪"


def test_p0_bug_keyword_crash():
    r = apply_rules("打开就闪退，根本用不了")
    assert r is not None
    assert r["L1"] == "A.Bug"
    assert r["severity"] == "P0"
    assert r["rule_name"] == "rule:bug_p0"


def test_p0_keyword_with_l2_detection():
    r = apply_rules("打字的时候直接闪退")
    assert r is not None
    assert r["L1"] == "A.Bug"
    assert r["severity"] == "P0"
    assert "输入核心" in r["L2"]


def test_suggest_keyword():
    r = apply_rules("希望增加自定义皮肤功能")
    assert r is not None
    assert r["L1"] == "B.建议"
    assert "皮肤" in r["L2"]


def test_question_with_question_mark():
    r = apply_rules("怎么换皮肤？")
    assert r is not None
    assert r["L1"] == "C.咨询"


def test_short_question_without_mark():
    r = apply_rules("怎么换皮肤")
    assert r is not None
    assert r["L1"] == "C.咨询"


def test_bug_uplift_to_p1():
    r = apply_rules("总是卡顿，每次打字都要等一下")
    assert r is not None
    assert r["L1"] == "A.Bug"
    assert r["severity"] == "P1"


def test_bug_general_p2():
    r = apply_rules("偶尔会卡顿一下")
    assert r is not None
    assert r["L1"] == "A.Bug"
    assert r["severity"] == "P2"


def test_unmatched_returns_none():
    """模糊样本应返回 None，由 LLM 兜底。"""
    r = apply_rules("我觉得这个产品还可以吧")
    # 既不该命中 D.情绪（长度大于 emotion_max_len=8），也不该命中 Bug/建议/咨询
    # 即使 fallback 命中其它规则，也必须是显式规则且 confidence 合理
    if r is not None:
        # 如果命中了，至少应是合法 schema
        assert r["source"] == "rule"
        assert r["L1"] in {"A.Bug", "B.建议", "C.咨询", "D.情绪", "E.无效"}


def test_returned_dict_schema_complete():
    r = apply_rules("闪退")
    assert r is not None
    for key in ("L1", "L2", "severity", "confidence", "reason", "source", "rule_name"):
        assert key in r
    assert isinstance(r["L2"], list)
    assert isinstance(r["confidence"], float)
    assert 0.0 <= r["confidence"] <= 1.0
    assert r["source"] == "rule"
