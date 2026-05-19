"""run_tagging 端到端集成测试（用注入的 mock LLM，不打真实远端）。"""
from __future__ import annotations

import time

import pytest

from feedback_hub import db
from feedback_hub.tagger import pipeline


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("feedback_hub.config.DB_PATH", tmp_path / "feedback.db")
    c = db.connect(tmp_path / "feedback.db")
    db.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _seed_feedback(conn, feedback_id, text, user_vid="u1", ts_ms=1000):
    row = {
        "feedback_id": feedback_id,
        "conversation_id": "tmp",  # 占位，pipeline 会重算
        "msg_seq": 0,
        "channel": "wetype",
        "ts_ms": ts_ms,
        "platform": "iOS",
        "appversion": "1.0",
        "user_vid": user_vid,
        "keyboard_source": "",
        "device_name": "",
        "channelid": "",
        "enginever": "",
        "msgtype": "text",
        "text": text,
        "tags": "",
        "raw_json": "{}",
        "pulled_at": int(time.time()),
    }
    db.upsert_feedback(conn, row)


def test_run_tagging_empty_db_returns_zero(conn):
    stats = pipeline.run_tagging(conn, llm_call=lambda t: '{}')
    assert stats == {
        "total": 0, "tagged": 0,
        "by_rule": 0, "by_llm": 0, "pending": 0,
        "conversations_recomputed": 0, "user_vids_recomputed": 0,
    }


def test_run_tagging_rule_only_path(conn):
    _seed_feedback(conn, "fb1", "闪退", user_vid="u1", ts_ms=1000)
    _seed_feedback(conn, "fb2", "希望增加皮肤", user_vid="u1", ts_ms=2000)
    conn.commit()

    def mock_llm(text):
        raise AssertionError("规则应命中，不该调 LLM")

    stats = pipeline.run_tagging(conn, llm_call=mock_llm)
    assert stats["total"] == 2
    assert stats["tagged"] == 2
    assert stats["by_rule"] == 2

    # 验证 message_label 写入
    rows = conn.execute("SELECT feedback_id, L1, severity FROM message_label ORDER BY feedback_id").fetchall()
    assert (rows[0]["feedback_id"], rows[0]["L1"], rows[0]["severity"]) == ("fb1", "A.Bug", "P0")
    assert (rows[1]["feedback_id"], rows[1]["L1"]) == ("fb2", "B.建议")

    # 验证 conversation_id 重算（同一 user_vid + 短间隔，应是同一个会话）
    cids = {r["conversation_id"] for r in conn.execute("SELECT conversation_id FROM feedback").fetchall()}
    assert len(cids) == 1

    # 验证 conversation_label 聚合（A.Bug 优先级高于 B.建议）
    cl = conn.execute("SELECT * FROM conversation_label").fetchall()
    assert len(cl) == 1
    assert cl[0]["L1"] == "A.Bug"
    assert cl[0]["severity"] == "P0"
    assert cl[0]["msg_count"] == 2


def test_run_tagging_llm_fallback_path(conn):
    _seed_feedback(conn, "fb_x", "我有点话想说不太好讲清楚", user_vid="u1", ts_ms=1000)
    conn.commit()

    def mock_llm(text):
        return '```json\n{"L1":"D.情绪","L2":[],"severity":"P3","confidence":0.6,"reason":"模糊"}\n```'

    stats = pipeline.run_tagging(conn, llm_call=mock_llm)
    assert stats["tagged"] == 1
    assert stats["by_llm"] == 1
    rows = conn.execute("SELECT L1, source FROM message_label").fetchall()
    assert rows[0]["L1"] == "D.情绪"
    assert rows[0]["source"] == "llm"


def test_run_tagging_llm_failure_returns_pending(conn):
    _seed_feedback(conn, "fb_y", "写得很模糊的反馈内容", user_vid="u2", ts_ms=1000)
    conn.commit()

    def mock_llm(text):
        raise RuntimeError("agent down")

    stats = pipeline.run_tagging(conn, llm_call=mock_llm)
    assert stats["pending"] == 1
    rows = conn.execute("SELECT L1, reason FROM message_label").fetchall()
    assert rows[0]["L1"] == "待定"
    assert "llm_error" in rows[0]["reason"]


def test_run_tagging_idempotent_on_second_run(conn):
    _seed_feedback(conn, "fb1", "闪退", user_vid="u1", ts_ms=1000)
    conn.commit()
    pipeline.run_tagging(conn, llm_call=lambda t: '{}')
    n1 = conn.execute("SELECT COUNT(*) FROM message_label").fetchone()[0]
    # 第二次跑——所有 feedback 都已被打标，应该 0 个新打标
    stats2 = pipeline.run_tagging(conn, llm_call=lambda t: '{}')
    n2 = conn.execute("SELECT COUNT(*) FROM message_label").fetchone()[0]
    assert n1 == 1
    assert n2 == 1
    assert stats2["total"] == 0


def test_run_tagging_recomputes_conversation_across_users(conn):
    """两个 user，分别一条；应产生两个会话标签。"""
    _seed_feedback(conn, "fb_a", "闪退", user_vid="u1", ts_ms=1000)
    _seed_feedback(conn, "fb_b", "希望加皮肤", user_vid="u2", ts_ms=2000)
    conn.commit()

    pipeline.run_tagging(conn, llm_call=lambda t: '{}')
    rows = conn.execute("SELECT user_vid, L1 FROM conversation_label ORDER BY user_vid").fetchall()
    assert len(rows) == 2
    by_user = {r["user_vid"]: r["L1"] for r in rows}
    assert by_user["u1"] == "A.Bug"
    assert by_user["u2"] == "B.建议"


def test_run_tagging_split_conversation_by_gap(conn):
    """同 user 的两条消息相隔超过 30min → 两个会话。"""
    _seed_feedback(conn, "fb_a", "闪退", user_vid="u1", ts_ms=0)
    _seed_feedback(conn, "fb_b", "希望加皮肤", user_vid="u1", ts_ms=1801 * 1000)
    conn.commit()

    pipeline.run_tagging(conn, llm_call=lambda t: '{}')
    rows = conn.execute("SELECT * FROM conversation_label").fetchall()
    assert len(rows) == 2
