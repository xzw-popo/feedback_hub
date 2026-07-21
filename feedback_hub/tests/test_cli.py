"""轻量 CLI 测试：argparse 路由 + tag 离线模式（不调用真实远端）。"""
from __future__ import annotations

import builtins
import json
import sys
from datetime import datetime, timedelta, timezone
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


def test_pull_subcommand_with_mocked_fetch(monkeypatch, tmp_path, capsys):
    from feedback_hub import puller

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def fake_fetch(s_sec, e_sec, **kw):
        return {"errCode": 0, "results": []}

    monkeypatch.setattr(puller, "fetch_window", fake_fetch)
    monkeypatch.setattr(puller, "RAW_DIR", raw_dir)
    monkeypatch.setattr(puller, "ensure_dirs", lambda: None)
    rc = cli.main(["pull", "--last", "1h"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"fetched_count": 0' in out


def test_unknown_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main(["unknown"])


def _incremental_result(**changes):
    """Use the production result shape so CLI serialization stays honest."""
    from feedback_hub.incremental_pipeline import IncrementalRunResult

    values = {
        "run_id": "run-1", "status": "succeeded",
        "started_at": "2026-07-21T00:00:00+00:00",
        "finished_at": "2026-07-21T00:00:01+00:00",
        "window_start": "2026-07-20T23:30:00+00:00",
        "window_end": "2026-07-21T00:00:00+00:00",
        "pull_status": "succeeded", "vector_status": "succeeded",
        "fetched_count": 3, "inserted_count": 2, "skipped_dup_count": 1,
        "vectorized_count": 2, "source_generation_ms": 100,
        "vector_watermark_ms": 1234, "error_code": "",
    }
    values.update(changes)
    return IncrementalRunResult(**values)


def _one_json(capsys):
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    return json.loads(captured.out), captured.err


def test_incremental_cli_defaults_to_exact_utc_thirty_minutes(monkeypatch, capsys):
    observed = {}

    def fake_run(**kwargs):
        observed.update(kwargs)
        return _incremental_result()

    monkeypatch.setattr("feedback_hub.incremental_pipeline.run_incremental", fake_run)
    assert cli.main(["pipeline", "incremental"]) == 0
    payload, stderr = _one_json(capsys)
    assert stderr == ""
    assert payload["window_minutes"] == 30
    assert payload["window_seconds"] == 1800
    assert payload["status"] == "succeeded"
    assert observed["pull_window"] == timedelta(minutes=30)
    assert observed["now"].tzinfo == timezone.utc
    assert observed["channel"] == "wetype"


def test_incremental_cli_serializes_vector_failure_once(monkeypatch, capsys):
    from feedback_hub.incremental_pipeline import IncrementalPipelineError

    result = _incremental_result(status="failed", vector_status="failed", error_code="RuntimeError")
    monkeypatch.setattr(
        "feedback_hub.incremental_pipeline.run_incremental",
        lambda **_kwargs: (_ for _ in ()).throw(IncrementalPipelineError(result)),
    )
    assert cli.main(["pipeline", "incremental"]) == 1
    payload, stderr = _one_json(capsys)
    assert payload["ok"] is False
    assert payload["run_id"] == "run-1"
    assert payload["vector_status"] == "failed"
    assert payload["window_seconds"] == 1800
    assert "incremental pipeline failed" in stderr


def test_incremental_cli_marks_an_incomplete_vector_result_nonzero(monkeypatch, capsys):
    result = _incremental_result(status="partial", vector_status="partial")
    monkeypatch.setattr("feedback_hub.incremental_pipeline.run_incremental", lambda **_kwargs: result)
    assert cli.main(["pipeline", "incremental"]) == 1
    payload, stderr = _one_json(capsys)
    assert payload["ok"] is False
    assert payload["status"] == "partial"
    assert "incomplete" in stderr


@pytest.mark.parametrize(("window", "seconds", "minutes"), [
    ("90s", 90, 1.5),
    ("30s", 30, 0.5),
])
def test_incremental_cli_reports_exact_non_minute_window_duration(monkeypatch, capsys, window, seconds, minutes):
    monkeypatch.setattr("feedback_hub.incremental_pipeline.run_incremental", lambda **_kwargs: _incremental_result())
    assert cli.main(["pipeline", "incremental", "--pull-window", window]) == 0
    payload, _ = _one_json(capsys)
    assert payload["window_seconds"] == seconds
    assert payload["window_minutes"] == minutes


def test_incremental_cli_serializes_pull_failure_once(monkeypatch, capsys):
    from feedback_hub.incremental_pipeline import IncrementalPipelineError

    result = _incremental_result(
        status="failed", pull_status="failed", vector_status="not_started", error_code="PullError",
    )
    monkeypatch.setattr(
        "feedback_hub.incremental_pipeline.run_incremental",
        lambda **_kwargs: (_ for _ in ()).throw(IncrementalPipelineError(result)),
    )
    assert cli.main(["pipeline", "incremental"]) == 1
    payload, stderr = _one_json(capsys)
    assert payload["run_id"] == "run-1"
    assert payload["window_start"] == "2026-07-20T23:30:00+00:00"
    assert payload["pull_status"] == "failed"
    assert payload["window_seconds"] == 1800
    assert "incremental pipeline failed: pull stage" in stderr


def test_incremental_cli_reports_lock_contention_with_cron_exit_code(monkeypatch, capsys):
    from feedback_hub.incremental_pipeline import PipelineAlreadyRunning

    monkeypatch.setattr(
        "feedback_hub.incremental_pipeline.run_incremental",
        lambda **_kwargs: (_ for _ in ()).throw(PipelineAlreadyRunning("held")),
    )
    assert cli.main(["pipeline", "incremental"]) == 75
    payload, stderr = _one_json(capsys)
    assert payload == {
        "command": "pipeline incremental", "ok": False, "status": "skipped_locked",
        "window_minutes": 30, "window_seconds": 1800,
    }
    assert "already running" in stderr


@pytest.mark.parametrize("argv", [
    ["pipeline", "incremental", "--pull-window", "0m"],
    ["pipeline", "incremental", "--pull-window", "nanm"],
    ["ingest", "backfill", "--last", "367d"],
    ["ingest", "backfill", "--last", "1h", "--chunk", "0s"],
    ["pipeline", "incremental", "--channel", "../unsafe"],
    ["pipeline", "incremental", "--unknown"],
])
def test_new_pipeline_parser_and_duration_errors_are_one_json(argv, capsys):
    assert cli.main(argv) == 1
    payload, stderr = _one_json(capsys)
    assert payload["ok"] is False
    assert payload["command"] in {"pipeline incremental", "ingest backfill"}
    assert payload["error"] == "ArgumentError"
    assert stderr


def test_ingest_backfill_uses_utc_current_time_and_channel(monkeypatch, capsys):
    from feedback_hub.incremental_pipeline import BackfillResult

    observed = {}

    def fake_backfill(**kwargs):
        observed.update(kwargs)
        return BackfillResult(
            start="2026-07-19T00:00:00+00:00", end="2026-07-21T00:00:00+00:00",
            chunk="PT6H", completed_windows=8, skipped_covered_windows=0,
        )

    monkeypatch.setattr("feedback_hub.incremental_pipeline.backfill_coverage", fake_backfill)
    assert cli.main(["ingest", "backfill", "--last", "2d", "--chunk", "6h", "--channel", "pc"]) == 0
    payload, stderr = _one_json(capsys)
    assert stderr == ""
    assert payload["ok"] is True
    assert payload["completed_windows"] == 8
    assert observed["end"].tzinfo == timezone.utc
    assert observed["end"] - observed["start"] == timedelta(days=2)
    assert observed["chunk"] == timedelta(hours=6)
    assert observed["channel"] == "pc"


def test_ingest_pull_is_exactly_the_legacy_pull_route_and_argument_contract(monkeypatch, capsys):
    from feedback_hub import puller

    legacy = cli.build_parser().parse_args(["pull", "--last", "1h", "--channel", "pc", "--service-vid", "7"])
    ingest = cli.build_parser().parse_args(["ingest", "pull", "--last", "1h", "--channel", "pc", "--service-vid", "7"])
    assert legacy.func is cli.cmd_pull
    assert ingest.func is cli.cmd_pull
    assert (legacy.last, legacy.channel, legacy.service_vid) == (ingest.last, ingest.channel, ingest.service_vid)

    monkeypatch.setattr(puller, "pull", lambda *_args, **_kwargs: {"fetched_count": 0, "raw_dump": "fixed"})
    monkeypatch.setattr(cli, "_resolve_window", lambda _args: (datetime(2026, 7, 21, 8), datetime(2026, 7, 21, 9)))
    assert cli.main(["pull", "--last", "1h"]) == 0
    legacy_out, legacy_err = capsys.readouterr()
    assert cli.main(["ingest", "pull", "--last", "1h"]) == 0
    ingest_out, ingest_err = capsys.readouterr()
    assert (legacy_out, legacy_err) == (ingest_out, ingest_err)


def test_backfill_rejects_more_than_one_thousand_windows_before_pipeline_import(monkeypatch, capsys):
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "feedback_hub.incremental_pipeline":
            raise AssertionError("unbounded backfill imported pipeline")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    assert cli.main(["ingest", "backfill", "--last", "251d", "--chunk", "6h"]) == 1
    payload, stderr = _one_json(capsys)
    assert payload == {"command": "ingest backfill", "error": "ArgumentError", "ok": False}
    assert "argument error" in stderr
