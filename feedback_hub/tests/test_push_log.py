"""测试 pusher.push_log：把 CandidateGroup 落 push_log 表的业务语义。"""
from __future__ import annotations

import pytest

from feedback_hub import db
from feedback_hub.pusher import push_log, scorer


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "f.db"
    monkeypatch.setattr("feedback_hub.config.DB_PATH", db_path)
    c = db.connect(db_path)
    db.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _group(sig=("crash", "输入核心", "5.4"), *, dup=12, p0=8,
           cross=1, versions=None, rep_text="t",
           rep_conv="c1", rep_fid="f1", score=42.0):
    if versions is None:
        versions = {"5.4.0": 3, "5.4.1": 9}
    return scorer.CandidateGroup(
        signature=sig, conversations=[], score=score,
        dup_count=dup, p0_count=p0, cross_version=cross, recent_24h=1,
        affected_versions=versions,
        representative_text=rep_text,
        representative_conv_id=rep_conv,
        representative_feedback_id=rep_fid,
    )


def test_save_top_groups_returns_ids_in_order(conn):
    groups = [_group(sig=("crash", "输入核心", "5.4")),
              _group(sig=("lag", "性能", "5.4"))]
    ids = push_log.save_top_groups(
        conn, push_date="2026-05-22",
        top_groups=groups, created_at=100,
    )
    assert len(ids) == 2
    rows = conn.execute(
        "SELECT id, rank, signature, group_id, primary_l2, major_version, "
        "score, dup_count, p0_count, cross_version, "
        "affected_versions, representative_text, "
        "representative_conversation_id, representative_feedback_id, "
        "created_at, delivered_at, is_empty "
        "FROM push_log ORDER BY rank ASC"
    ).fetchall()
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["signature"] == "crash|输入核心|5.4"
    assert rows[0]["group_id"] == "crash"
    assert rows[0]["primary_l2"] == "输入核心"
    assert rows[0]["major_version"] == "5.4"
    assert rows[0]["dup_count"] == 12
    assert rows[0]["p0_count"] == 8
    assert rows[0]["cross_version"] == 1
    assert rows[0]["created_at"] == 100
    assert rows[0]["delivered_at"] is None
    assert rows[0]["is_empty"] == 0


def test_save_top_groups_serializes_affected_versions(conn):
    groups = [_group(versions={"5.4.0": 3, "5.4.1": 9})]
    push_log.save_top_groups(
        conn, push_date="2026-05-22", top_groups=groups, created_at=100,
    )
    row = conn.execute(
        "SELECT affected_versions FROM push_log"
    ).fetchone()
    # 按 count 降序：5.4.1:9 在前
    assert row["affected_versions"] == "5.4.1:9|5.4.0:3"


def test_save_empty_inserts_placeholder(conn):
    ids = push_log.save_top_groups(
        conn, push_date="2026-05-22", top_groups=[], created_at=100,
    )
    assert len(ids) == 1
    row = conn.execute(
        "SELECT rank, signature, is_empty, representative_text "
        "FROM push_log WHERE id=?", (ids[0],),
    ).fetchone()
    assert row["rank"] == 0
    assert row["is_empty"] == 1
    assert row["signature"] is None


def test_mark_delivered_updates_all_ids(conn):
    groups = [_group(sig=("crash", "a", "1")),
              _group(sig=("lag", "b", "1"))]
    ids = push_log.save_top_groups(
        conn, push_date="2026-05-22", top_groups=groups, created_at=100,
    )
    push_log.mark_delivered(conn, ids, delivered_at=200)
    rows = conn.execute(
        "SELECT delivered_at FROM push_log ORDER BY id"
    ).fetchall()
    assert all(r["delivered_at"] == 200 for r in rows)
