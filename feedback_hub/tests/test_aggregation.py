"""测试会话级标签聚合（spec §4.3 规则）。"""
from __future__ import annotations

import time

from feedback_hub.tagger.pipeline import aggregate_conversation_label


def _ml(feedback_id, l1, l2, severity, conf, reason, ts_ms, msg_seq=0,
        user_vid="u1", appversion="1.0", channel="wetype"):
    """构造一条 message_label-with-feedback 复合行。"""
    return {
        "feedback_id": feedback_id,
        "L1": l1, "L2": l2, "severity": severity,
        "confidence": conf, "reason": reason,
        "ts_ms": ts_ms, "msg_seq": msg_seq,
        "user_vid": user_vid, "appversion": appversion, "channel": channel,
    }


def test_single_message_aggregates_directly():
    rows = [_ml("a", "A.Bug", "输入核心", "P1", 0.8, "卡顿", 1000, msg_seq=0)]
    out = aggregate_conversation_label("conv1", rows)
    assert out["conversation_id"] == "conv1"
    assert out["L1"] == "A.Bug"
    assert out["severity"] == "P1"
    assert out["confidence"] == 0.8
    assert out["reason"] == "卡顿"
    assert out["L2"] == "输入核心"
    assert out["msg_count"] == 1
    assert out["first_ts_ms"] == 1000
    assert out["last_ts_ms"] == 1000
    assert out["user_vid"] == "u1"
    assert out["appversion"] == "1.0"
    assert out["channel"] == "wetype"
    assert out["source"] == "aggregated"


def test_l1_picks_most_severe():
    """A.Bug > B.建议 > C.咨询 > D.情绪 > E.无效 > 待定"""
    rows = [
        _ml("a", "C.咨询", "", "P3", 0.7, "问问", 1000, msg_seq=0),
        _ml("b", "B.建议", "皮肤", "P3", 0.8, "建议", 2000, msg_seq=1),
        _ml("c", "A.Bug", "性能", "P1", 0.85, "卡顿", 3000, msg_seq=2),
    ]
    out = aggregate_conversation_label("conv1", rows)
    assert out["L1"] == "A.Bug"
    assert out["confidence"] == 0.85
    assert out["reason"] == "卡顿"


def test_severity_picks_most_severe():
    """P0 > P1 > P2 > P3"""
    rows = [
        _ml("a", "A.Bug", "性能", "P2", 0.7, "偶尔卡", 1000, msg_seq=0),
        _ml("b", "A.Bug", "性能", "P0", 0.95, "闪退", 2000, msg_seq=1),
        _ml("c", "A.Bug", "性能", "P1", 0.85, "卡顿", 3000, msg_seq=2),
    ]
    out = aggregate_conversation_label("conv1", rows)
    assert out["severity"] == "P0"
    assert out["confidence"] == 0.95
    assert out["reason"] == "闪退"


def test_l2_is_union_pipe_separated():
    rows = [
        _ml("a", "A.Bug", "输入核心", "P2", 0.7, "x", 1000, msg_seq=0),
        _ml("b", "A.Bug", "性能", "P2", 0.7, "y", 2000, msg_seq=1),
        _ml("c", "A.Bug", "输入核心|键盘交互", "P2", 0.7, "z", 3000, msg_seq=2),
    ]
    out = aggregate_conversation_label("conv1", rows)
    parts = set(out["L2"].split("|"))
    assert parts == {"输入核心", "性能", "键盘交互"}


def test_when_multiple_same_l1_picks_highest_confidence():
    rows = [
        _ml("a", "A.Bug", "性能", "P1", 0.6, "证据弱", 1000, msg_seq=0),
        _ml("b", "A.Bug", "性能", "P1", 0.95, "证据强", 2000, msg_seq=1),
        _ml("c", "A.Bug", "性能", "P1", 0.7, "中等", 3000, msg_seq=2),
    ]
    out = aggregate_conversation_label("conv1", rows)
    assert out["confidence"] == 0.95
    assert out["reason"] == "证据强"


def test_msg_count_and_ts_range():
    rows = [
        _ml("a", "A.Bug", "性能", "P2", 0.7, "x", 5000, msg_seq=0),
        _ml("b", "A.Bug", "性能", "P2", 0.7, "y", 1000, msg_seq=1),
        _ml("c", "A.Bug", "性能", "P2", 0.7, "z", 3000, msg_seq=2),
    ]
    out = aggregate_conversation_label("conv1", rows)
    assert out["msg_count"] == 3
    assert out["first_ts_ms"] == 1000
    assert out["last_ts_ms"] == 5000


def test_metadata_taken_from_msg_seq_zero():
    """user_vid / appversion / channel 取 msg_seq=0 那条；spec §4.3"""
    rows = [
        _ml("a", "A.Bug", "x", "P2", 0.7, "_", 5000, msg_seq=2,
            user_vid="u_late", appversion="2.0", channel="wetype"),
        _ml("b", "A.Bug", "x", "P2", 0.7, "_", 1000, msg_seq=0,
            user_vid="u_early", appversion="1.5", channel="wetype"),
        _ml("c", "A.Bug", "x", "P2", 0.7, "_", 3000, msg_seq=1,
            user_vid="u_mid", appversion="1.8", channel="wetype"),
    ]
    out = aggregate_conversation_label("conv1", rows)
    assert out["user_vid"] == "u_early"
    assert out["appversion"] == "1.5"


def test_aggregated_at_is_recent():
    rows = [_ml("a", "A.Bug", "x", "P2", 0.7, "_", 1000, msg_seq=0)]
    before = int(time.time())
    out = aggregate_conversation_label("conv1", rows)
    after = int(time.time())
    assert before <= out["aggregated_at"] <= after


def test_pending_l1_is_lowest_priority():
    rows = [
        _ml("a", "待定", "", "P3", 0.0, "无法判定", 1000, msg_seq=0),
        _ml("b", "E.无效", "其他", "P3", 0.9, "灌水", 2000, msg_seq=1),
    ]
    out = aggregate_conversation_label("conv1", rows)
    assert out["L1"] == "E.无效"


def test_empty_rows_returns_none():
    assert aggregate_conversation_label("conv1", []) is None
