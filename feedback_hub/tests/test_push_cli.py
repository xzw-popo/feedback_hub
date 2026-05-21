"""端到端测：cli push 子命令的三种模式（dry-run / no-send / 真实发送）。"""
from __future__ import annotations

import time

import pytest

from feedback_hub import cli, db


@pytest.fixture(autouse=True)
def _patch_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("feedback_hub.config.DB_PATH", tmp_path / "fb.db")
    monkeypatch.setattr("feedback_hub.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("feedback_hub.config.RAW_DIR", tmp_path / "raw")


@pytest.fixture(autouse=True)
def _reset_yaml_cache():
    from feedback_hub.pusher.config_loader import reset_cache
    reset_cache()
    yield
    reset_cache()


def _seed_one_p0_conv(conn, conv_id="c1", text="一直闪退",
                      appversion="5.4.1", ts_ms=None):
    """seed 一个能进 Top 5 候选的 P0 会话。"""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    now = int(time.time())
    db.upsert_feedback(conn, {
        "feedback_id": f"{conv_id}-0",
        "conversation_id": conv_id, "msg_seq": 0,
        "channel": "wetype", "ts_ms": ts_ms,
        "platform": "iOS", "appversion": appversion, "user_vid": "u",
        "keyboard_source": "", "device_name": "", "channelid": "",
        "enginever": "", "msgtype": "text", "text": text,
        "tags": "", "raw_json": "{}", "pulled_at": now,
    })
    db.upsert_message_label(conn, {
        "feedback_id": f"{conv_id}-0", "L1": "A.Bug", "L2": "输入核心",
        "severity": "P0", "confidence": 0.9, "reason": "test",
        "source": "rule", "rule_name": None, "tagged_at": now,
    })
    db.upsert_conversation_label(conn, {
        "conversation_id": conv_id, "L1": "A.Bug", "L2": "输入核心",
        "severity": "P0", "confidence": 0.9, "reason": "test",
        "source": "aggregated", "msg_count": 1,
        "first_ts_ms": ts_ms, "last_ts_ms": ts_ms,
        "user_vid": "u", "appversion": appversion,
        "channel": "wetype", "aggregated_at": now,
    })
    conn.commit()


def test_push_dry_run_no_db_write(capsys):
    """dry-run 既不写 push_log 也不发送。"""
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    rc = cli.main(["push", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Top Bug" in out or "今日无 Top Bug" in out

    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM push_log").fetchone()[0]
    conn.close()
    assert n == 0


def test_push_no_send_writes_log_no_webhook(monkeypatch, capsys):
    """--no-send 写 push_log，但不调 webhook。"""
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    sent = {"called": False}

    def fake_send(**kwargs):
        sent["called"] = True
        return True

    monkeypatch.setattr(
        "feedback_hub.pusher.webhook.send_markdown", fake_send,
    )

    rc = cli.main(["push", "--no-send"])
    assert rc == 0
    assert sent["called"] is False

    conn = db.connect()
    rows = conn.execute(
        "SELECT delivered_at, is_empty FROM push_log"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1
    assert all(r["delivered_at"] is None for r in rows)


def test_push_real_marks_delivered_on_success(monkeypatch):
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://fake")
    monkeypatch.setattr(
        "feedback_hub.pusher.webhook.send_markdown",
        lambda **kw: True,
    )

    rc = cli.main(["push"])
    assert rc == 0

    conn = db.connect()
    rows = conn.execute(
        "SELECT delivered_at FROM push_log"
    ).fetchall()
    conn.close()
    assert all(r["delivered_at"] is not None for r in rows)


def test_push_real_keeps_delivered_null_on_failure(monkeypatch):
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    monkeypatch.setenv("WECHAT_WEBHOOK_URL", "https://fake")
    monkeypatch.setattr(
        "feedback_hub.pusher.webhook.send_markdown",
        lambda **kw: False,
    )

    rc = cli.main(["push"])
    assert rc == 1  # 失败时返回非 0

    conn = db.connect()
    rows = conn.execute(
        "SELECT delivered_at FROM push_log"
    ).fetchall()
    conn.close()
    assert all(r["delivered_at"] is None for r in rows)


def test_push_no_webhook_url_aborts_real_mode(monkeypatch, capsys):
    """没设环境变量时，默认模式应报错退出。"""
    monkeypatch.delenv("WECHAT_WEBHOOK_URL", raising=False)
    rc = cli.main(["push"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "WECHAT_WEBHOOK_URL" in err


def test_push_dry_run_works_without_webhook_url(monkeypatch):
    """dry-run 不需要 webhook url。"""
    monkeypatch.delenv("WECHAT_WEBHOOK_URL", raising=False)
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    rc = cli.main(["push", "--dry-run"])
    assert rc == 0


def test_push_with_explicit_date(monkeypatch):
    conn = db.connect()
    db.init_schema(conn)
    _seed_one_p0_conv(conn)
    conn.close()

    rc = cli.main(["push", "--date", "2026-05-20", "--no-send"])
    assert rc == 0

    conn = db.connect()
    row = conn.execute(
        "SELECT push_date FROM push_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row["push_date"] == "2026-05-20"
