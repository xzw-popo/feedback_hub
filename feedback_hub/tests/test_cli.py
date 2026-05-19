"""轻量 CLI 测试：argparse 路由 + tag 离线模式（不调用真实远端）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from feedback_hub import cli, db


@pytest.fixture(autouse=True)
def _patch_data_dir(tmp_path, monkeypatch):
    """把 DB_PATH 指向 tmp，避免污染真实 data/feedback.db。"""
    monkeypatch.setattr("feedback_hub.config.DB_PATH", tmp_path / "fb.db")
    monkeypatch.setattr("feedback_hub.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("feedback_hub.config.RAW_DIR", tmp_path / "raw")


def test_parser_help_lists_all_subcommands(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "pull" in out and "tag" in out and "serve" in out


def test_tag_offline_mode_runs_without_data(capsys):
    rc = cli.main(["tag"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"total": 0' in out


def test_tag_offline_with_seeded_data(monkeypatch, capsys):
    """seed 一条命中规则的 feedback，tag 离线跑应能写出 message_label + conversation_label。"""
    import time as _t
    conn = db.connect()
    db.init_schema(conn)
    db.upsert_feedback(conn, {
        "feedback_id": "fb_x", "conversation_id": "p", "msg_seq": 0,
        "channel": "wetype", "ts_ms": 1000, "platform": "iOS",
        "appversion": "1", "user_vid": "u1",
        "keyboard_source": "", "device_name": "", "channelid": "",
        "enginever": "", "msgtype": "text", "text": "闪退",
        "tags": "", "raw_json": "{}", "pulled_at": int(_t.time()),
    })
    conn.commit()
    rc = cli.main(["tag"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"tagged": 1' in out
    assert '"by_rule": 1' in out
    rows = conn.execute("SELECT L1, severity FROM message_label").fetchall()
    assert rows[0]["L1"] == "A.Bug"
    cl = conn.execute("SELECT L1 FROM conversation_label").fetchone()
    assert cl["L1"] == "A.Bug"
    conn.close()


def test_pull_subcommand_with_mocked_fetch(monkeypatch, capsys):
    from feedback_hub import puller

    def fake_fetch(s_sec, e_sec, **kw):
        return {"errCode": 0, "results": []}

    monkeypatch.setattr(puller, "fetch_window", fake_fetch)
    rc = cli.main(["pull", "--last", "1h"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"fetched_count": 0' in out


def test_unknown_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main(["unknown"])
