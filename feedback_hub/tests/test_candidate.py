"""测试 pusher.candidate：SQL 拉取 + 与 scorer 串通。

集成性质——用临时 sqlite + 真实 schema，seed 几条 feedback /
message_label / conversation_label，验证 generate_candidates 能正确产出。
"""
from __future__ import annotations

import time

import pytest

from feedback_hub import db
from feedback_hub.pusher import candidate
from feedback_hub.pusher.config_loader import reset_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


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


def _seed_conv(conn, conv_id, *, msgs, l1="A.Bug", severity="P0",
               confidence=0.9, l2="输入核心", appversion="5.4.1",
               last_ts_ms=None, ts_base=2_000_000_000_000):
    """seed 一个会话的 feedback + message_label + conversation_label。

    msgs: list[(seq, text, msg_confidence)]
    """
    now = int(time.time())
    for seq, text, mconf in msgs:
        fid = f"{conv_id}-{seq}"
        ts_ms = ts_base + seq * 1000
        db.upsert_feedback(conn, {
            "feedback_id": fid, "conversation_id": conv_id, "msg_seq": seq,
            "channel": "wetype", "ts_ms": ts_ms,
            "platform": "iOS", "appversion": appversion,
            "user_vid": f"u-{conv_id}",
            "keyboard_source": "", "device_name": "", "channelid": "",
            "enginever": "", "msgtype": "text", "text": text,
            "tags": "", "raw_json": "{}", "pulled_at": now,
        })
        db.upsert_message_label(conn, {
            "feedback_id": fid, "L1": l1, "L2": l2, "severity": severity,
            "confidence": mconf, "reason": "test",
            "source": "rule", "rule_name": None, "tagged_at": now,
        })
    final_last_ts = (last_ts_ms if last_ts_ms is not None
                     else (ts_base + (len(msgs) - 1) * 1000))
    db.upsert_conversation_label(conn, {
        "conversation_id": conv_id, "L1": l1, "L2": l2,
        "severity": severity, "confidence": confidence,
        "reason": "test", "source": "aggregated",
        "msg_count": len(msgs),
        "first_ts_ms": ts_base, "last_ts_ms": final_last_ts,
        "user_vid": f"u-{conv_id}", "appversion": appversion,
        "channel": "wetype", "aggregated_at": now,
    })
    conn.commit()


def test_no_data_returns_empty(conn):
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0
    assert out["top_groups"] == []


def test_filters_non_bug_l1(conn):
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)], l1="B.建议")
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0


def test_filters_low_severity(conn):
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)], severity="P2")
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0


def test_filters_low_confidence(conn):
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)], confidence=0.5)
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 0


def test_filters_outside_7day_window(conn):
    now_ms = 2_000_000_000_000
    eight_days_ago = now_ms - 8 * 86_400_000
    _seed_conv(conn, "c1", msgs=[(0, "闪退", 0.9)],
               last_ts_ms=eight_days_ago, ts_base=eight_days_ago)
    out = candidate.generate_candidates(conn, now_ms=now_ms)
    assert out["scanned"] == 0


def test_full_text_concat_recovers_recall(conn):
    """方案 X 关键 case：首条无关键词，第三条有 → full_text 拼接命中 crash。"""
    _seed_conv(conn, "c1", msgs=[
        (0, "你好", 0.7),
        (1, "我用 5.4.1", 0.6),
        (2, "突然闪退了", 0.95),
    ])
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert out["scanned"] == 1
    assert len(out["top_groups"]) == 1
    g = out["top_groups"][0]
    assert g.signature == ("crash", "输入核心", "5.4")


def test_representative_text_is_top_confidence_message(conn):
    """方案 X 关键 case：representative_text 应是 confidence 最高那条原文。"""
    _seed_conv(conn, "c1", msgs=[
        (0, "你好", 0.7),
        (1, "我用 5.4.1", 0.6),
        (2, "突然闪退了", 0.95),
    ])
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    g = out["top_groups"][0]
    assert g.representative_text == "突然闪退了"
    assert g.representative_feedback_id == "c1-2"


def test_two_convs_same_signature_aggregate(conn):
    _seed_conv(conn, "a", msgs=[(0, "闪退啊", 0.9)], appversion="5.4.0")
    _seed_conv(conn, "b", msgs=[(0, "一直闪退", 0.9)], appversion="5.4.1")
    out = candidate.generate_candidates(conn, now_ms=2_000_000_000_000)
    assert len(out["top_groups"]) == 1
    g = out["top_groups"][0]
    assert g.dup_count == 2
    assert g.cross_version == 1


def test_truncates_to_top_5(conn):
    base = 2_000_000_000_000
    for i, l2 in enumerate(["输入核心", "语音", "皮肤", "词库", "账号", "性能"]):
        _seed_conv(conn, f"c{i}", msgs=[(0, "闪退", 0.9)],
                   l2=l2, appversion="5.4.0", ts_base=base)
    out = candidate.generate_candidates(conn, now_ms=base)
    assert len(out["top_groups"]) == 5
    assert out["scanned"] == 6
    assert out["all_groups_count"] == 6
