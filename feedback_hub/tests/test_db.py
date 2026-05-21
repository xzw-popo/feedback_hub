"""测试 db.py：连接初始化 + 三种 upsert helper。"""
from __future__ import annotations

import sqlite3
import time

import pytest

from feedback_hub import db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """每个测试用一个临时 sqlite 文件。"""
    db_path = tmp_path / "feedback.db"
    monkeypatch.setattr("feedback_hub.config.DB_PATH", db_path)
    c = db.connect(db_path)
    db.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def test_connect_returns_sqlite_connection(tmp_path):
    p = tmp_path / "x.db"
    c = db.connect(p)
    try:
        assert isinstance(c, sqlite3.Connection)
        assert p.exists()
    finally:
        c.close()


def test_connect_enables_foreign_keys(tmp_path):
    c = db.connect(tmp_path / "x.db")
    try:
        cur = c.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1
    finally:
        c.close()


def test_init_schema_creates_all_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"feedback", "message_label", "conversation_label", "label_history"} <= names


def test_init_schema_is_idempotent(conn):
    # 重复 init 不应报错
    db.init_schema(conn)
    db.init_schema(conn)


def _sample_feedback_row() -> dict:
    return {
        "feedback_id": "fb_001",
        "conversation_id": "conv_aaa",
        "msg_seq": 0,
        "channel": "wetype",
        "ts_ms": 1747526400000,
        "platform": "iOS",
        "appversion": "1.2.3",
        "user_vid": "u_xxx",
        "keyboard_source": "",
        "device_name": "iPhone15",
        "channelid": "",
        "enginever": "",
        "msgtype": "text",
        "text": "闪退了",
        "tags": "",
        "raw_json": "{}",
        "pulled_at": int(time.time()),
    }


def test_upsert_feedback_inserts_then_skips_on_duplicate(conn):
    row = _sample_feedback_row()
    inserted1 = db.upsert_feedback(conn, row)
    inserted2 = db.upsert_feedback(conn, row)
    assert inserted1 is True
    assert inserted2 is False  # 主键冲突应跳过，行数不增加
    n = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    assert n == 1


def test_upsert_feedback_persists_all_columns(conn):
    row = _sample_feedback_row()
    db.upsert_feedback(conn, row)
    got = conn.execute(
        "SELECT feedback_id, channel, ts_ms, text, conversation_id FROM feedback"
    ).fetchone()
    assert tuple(got) == ("fb_001", "wetype", 1747526400000, "闪退了", "conv_aaa")


def test_upsert_message_label_overwrites_on_conflict(conn):
    db.upsert_feedback(conn, _sample_feedback_row())
    db.upsert_message_label(conn, {
        "feedback_id": "fb_001",
        "L1": "A.Bug", "L2": "性能",
        "severity": "P0", "confidence": 0.9,
        "reason": "P0 关键词", "source": "rule",
        "rule_name": "p0_keyword", "tagged_at": 1000,
    })
    db.upsert_message_label(conn, {
        "feedback_id": "fb_001",
        "L1": "B.建议", "L2": "",
        "severity": "P3", "confidence": 0.5,
        "reason": "重打标", "source": "llm",
        "rule_name": None, "tagged_at": 2000,
    })
    rows = conn.execute(
        "SELECT L1, severity, source, tagged_at FROM message_label WHERE feedback_id='fb_001'"
    ).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == ("B.建议", "P3", "llm", 2000)


def test_upsert_conversation_label_overwrites_on_conflict(conn):
    payload_v1 = {
        "conversation_id": "conv_aaa",
        "L1": "A.Bug", "L2": "性能",
        "severity": "P0", "confidence": 0.9, "reason": "old",
        "source": "aggregated", "msg_count": 1,
        "first_ts_ms": 1000, "last_ts_ms": 1000,
        "user_vid": "u_xxx", "appversion": "1.2.3",
        "channel": "wetype", "aggregated_at": 5000,
    }
    db.upsert_conversation_label(conn, payload_v1)
    payload_v2 = dict(payload_v1, msg_count=3, last_ts_ms=2000, reason="new", aggregated_at=6000)
    db.upsert_conversation_label(conn, payload_v2)
    got = conn.execute(
        "SELECT msg_count, last_ts_ms, reason, aggregated_at "
        "FROM conversation_label WHERE conversation_id='conv_aaa'"
    ).fetchone()
    assert tuple(got) == (3, 2000, "new", 6000)


def test_get_untagged_feedback_returns_only_unlabeled(conn):
    r = _sample_feedback_row()
    db.upsert_feedback(conn, r)
    db.upsert_feedback(conn, dict(r, feedback_id="fb_002", text="语音听不清"))
    db.upsert_feedback(conn, dict(r, feedback_id="fb_003", text="键盘卡顿"))
    db.upsert_message_label(conn, {
        "feedback_id": "fb_002",
        "L1": "A.Bug", "L2": "语音", "severity": "P1",
        "confidence": 0.8, "reason": "已打", "source": "rule",
        "rule_name": "kw", "tagged_at": 1,
    })
    rows = db.get_untagged_feedback(conn)
    ids = sorted(r["feedback_id"] for r in rows)
    assert ids == ["fb_001", "fb_003"]


def test_get_feedback_by_user_vid_returns_sorted_by_ts(conn):
    r = _sample_feedback_row()
    db.upsert_feedback(conn, dict(r, feedback_id="fb_a", ts_ms=3000))
    db.upsert_feedback(conn, dict(r, feedback_id="fb_b", ts_ms=1000))
    db.upsert_feedback(conn, dict(r, feedback_id="fb_c", ts_ms=2000))
    rows = db.get_feedback_by_user_vid(conn, "u_xxx")
    assert [x["feedback_id"] for x in rows] == ["fb_b", "fb_c", "fb_a"]


def test_update_conversation_assignment(conn):
    db.upsert_feedback(conn, _sample_feedback_row())
    db.update_conversation_assignment(conn, "fb_001", "conv_new", 7)
    got = conn.execute(
        "SELECT conversation_id, msg_seq FROM feedback WHERE feedback_id='fb_001'"
    ).fetchone()
    assert tuple(got) == ("conv_new", 7)


# ----------------------------- push_log --------------------------------

def test_push_log_table_exists(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "push_log" in names


def test_insert_push_log_returns_id(conn):
    pid = db.insert_push_log(conn, {
        "push_date": "2026-05-22", "rank": 1,
        "signature": "crash|输入核心|5.4",
        "group_id": "crash", "primary_l2": "输入核心", "major_version": "5.4",
        "representative_conversation_id": "c1", "representative_feedback_id": "f1",
        "score": 42.0, "dup_count": 12, "p0_count": 8, "cross_version": 1,
        "affected_versions": "5.4.0:3|5.4.1:9",
        "representative_text": "微信里打字突然闪退",
        "created_at": int(time.time()), "delivered_at": None, "is_empty": 0,
    })
    assert isinstance(pid, int) and pid > 0


def test_update_push_log_delivered(conn):
    pid = db.insert_push_log(conn, {
        "push_date": "2026-05-22", "rank": 1, "signature": "x",
        "group_id": "x", "primary_l2": "x", "major_version": "x",
        "representative_conversation_id": "c", "representative_feedback_id": "f",
        "score": 1.0, "dup_count": 1, "p0_count": 1, "cross_version": 0,
        "affected_versions": "1.0:1", "representative_text": "t",
        "created_at": 100, "delivered_at": None, "is_empty": 0,
    })
    db.update_push_log_delivered(conn, pid, 200)
    row = conn.execute(
        "SELECT delivered_at FROM push_log WHERE id=?", (pid,),
    ).fetchone()
    assert row["delivered_at"] == 200


def test_push_log_is_empty_placeholder(conn):
    pid = db.insert_push_log(conn, {
        "push_date": "2026-05-22", "rank": 0,
        "signature": None, "group_id": None, "primary_l2": None,
        "major_version": None,
        "representative_conversation_id": None, "representative_feedback_id": None,
        "score": None, "dup_count": None, "p0_count": None, "cross_version": None,
        "affected_versions": None, "representative_text": None,
        "created_at": 100, "delivered_at": None, "is_empty": 1,
    })
    row = conn.execute(
        "SELECT is_empty, signature FROM push_log WHERE id=?", (pid,),
    ).fetchone()
    assert row["is_empty"] == 1
    assert row["signature"] is None
