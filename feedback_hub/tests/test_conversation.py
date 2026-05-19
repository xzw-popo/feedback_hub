"""测试会话聚合算法（spec §3.2）。"""
from __future__ import annotations

import hashlib

import pytest

from feedback_hub.conversation import (
    assign_conversation_ids,
    make_conversation_id,
    make_anonymous_user_vid,
)


def _msg(feedback_id: str, user_vid: str | None, ts_ms: int) -> dict:
    return {"feedback_id": feedback_id, "user_vid": user_vid, "ts_ms": ts_ms}


def test_make_conversation_id_is_deterministic():
    a = make_conversation_id("u1", 1000)
    b = make_conversation_id("u1", 1000)
    assert a == b
    assert len(a) == 12


def test_make_conversation_id_changes_with_inputs():
    assert make_conversation_id("u1", 1000) != make_conversation_id("u2", 1000)
    assert make_conversation_id("u1", 1000) != make_conversation_id("u1", 2000)


def test_make_conversation_id_uses_sha1_first_12():
    expected = hashlib.sha1(b"u1:1000").hexdigest()[:12]
    assert make_conversation_id("u1", 1000) == expected


def test_make_anonymous_user_vid_is_deterministic():
    a = make_anonymous_user_vid("fb_xxx")
    b = make_anonymous_user_vid("fb_xxx")
    assert a == b
    assert a.startswith("anon_")
    assert len(a) == 5 + 12  # 'anon_' + 12 字符 sha1


def test_single_message_yields_one_conversation():
    msgs = [_msg("a", "u1", 1000)]
    out = assign_conversation_ids(msgs, gap_seconds=1800)
    assert len(out) == 1
    assert out[0]["conversation_id"] == make_conversation_id("u1", 1000)
    assert out[0]["msg_seq"] == 0


def test_two_close_messages_same_user_one_conversation():
    msgs = [_msg("a", "u1", 1000_000), _msg("b", "u1", 2000_000)]  # 1000 秒间隔 < 30 分钟
    out = assign_conversation_ids(msgs, gap_seconds=1800)
    assert out[0]["conversation_id"] == out[1]["conversation_id"]
    assert out[0]["msg_seq"] == 0
    assert out[1]["msg_seq"] == 1


def test_gap_exactly_threshold_stays_in_same_conversation():
    # 边界条件：gap == threshold 时 不切（spec §3.2 用 > 比较）
    gap_ms = 1800 * 1000
    msgs = [_msg("a", "u1", 0), _msg("b", "u1", gap_ms)]
    out = assign_conversation_ids(msgs, gap_seconds=1800)
    assert out[0]["conversation_id"] == out[1]["conversation_id"]


def test_gap_just_over_threshold_splits():
    gap_ms = 1800 * 1000 + 1
    msgs = [_msg("a", "u1", 0), _msg("b", "u1", gap_ms)]
    out = assign_conversation_ids(msgs, gap_seconds=1800)
    assert out[0]["conversation_id"] != out[1]["conversation_id"]
    assert out[0]["msg_seq"] == 0
    assert out[1]["msg_seq"] == 0  # 新会话从 0 重置


def test_two_users_get_independent_conversations():
    msgs = [_msg("a", "u1", 1000), _msg("b", "u2", 2000)]
    out = assign_conversation_ids(msgs)
    by_id = {m["feedback_id"]: m for m in out}
    assert by_id["a"]["conversation_id"] != by_id["b"]["conversation_id"]


def test_unsorted_input_is_handled_correctly():
    msgs = [
        _msg("c", "u1", 3000_000),
        _msg("a", "u1", 1000_000),
        _msg("b", "u1", 2000_000),
    ]
    out = assign_conversation_ids(msgs, gap_seconds=1800)
    # 输出顺序保留输入顺序，但 msg_seq 按时间确定
    by_id = {m["feedback_id"]: m for m in out}
    assert by_id["a"]["msg_seq"] == 0
    assert by_id["b"]["msg_seq"] == 1
    assert by_id["c"]["msg_seq"] == 2
    # 同一会话
    assert by_id["a"]["conversation_id"] == by_id["b"]["conversation_id"] == by_id["c"]["conversation_id"]
    # ID 来自最早 ts
    assert by_id["a"]["conversation_id"] == make_conversation_id("u1", 1000_000)


def test_missing_user_vid_falls_back_to_anonymous():
    msgs = [_msg("a", None, 1000), _msg("b", "", 2000)]
    out = assign_conversation_ids(msgs)
    by_id = {m["feedback_id"]: m for m in out}
    assert by_id["a"]["conversation_id"] != by_id["b"]["conversation_id"]
    # anon 模式下每条独立成会话
    assert by_id["a"]["msg_seq"] == 0
    assert by_id["b"]["msg_seq"] == 0


def test_three_segments_in_same_user():
    """同 user：第一段 2 条 → 大间隔 → 第二段 1 条 → 大间隔 → 第三段 2 条"""
    msgs = [
        _msg("a", "u1", 0),
        _msg("b", "u1", 600_000),  # +10 分钟，同段
        _msg("c", "u1", 600_000 + 2000_000),  # +33 分钟，新段
        _msg("d", "u1", 600_000 + 2000_000 + 2000_000),  # +33 分钟，再新段
        _msg("e", "u1", 600_000 + 2000_000 + 2000_000 + 60_000),  # +1 分钟，同段
    ]
    out = assign_conversation_ids(msgs, gap_seconds=1800)
    by_id = {m["feedback_id"]: m for m in out}
    assert by_id["a"]["conversation_id"] == by_id["b"]["conversation_id"]
    assert by_id["a"]["conversation_id"] != by_id["c"]["conversation_id"]
    assert by_id["c"]["conversation_id"] != by_id["d"]["conversation_id"]
    assert by_id["d"]["conversation_id"] == by_id["e"]["conversation_id"]
    # msg_seq 在新段重置
    assert by_id["c"]["msg_seq"] == 0
    assert by_id["d"]["msg_seq"] == 0
    assert by_id["e"]["msg_seq"] == 1


def test_idempotent_recompute():
    """对相同消息再次跑算法，应得到完全相同的 conversation_id（保证可重算）。"""
    msgs = [_msg("a", "u1", 1000), _msg("b", "u1", 2000)]
    out1 = assign_conversation_ids(msgs, gap_seconds=1800)
    out2 = assign_conversation_ids(msgs, gap_seconds=1800)
    ids1 = [m["conversation_id"] for m in out1]
    ids2 = [m["conversation_id"] for m in out2]
    assert ids1 == ids2
