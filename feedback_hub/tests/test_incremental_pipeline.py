from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from feedback_hub import db
from feedback_hub.incremental_pipeline import (
    IncrementalPipelineError,
    PipelineAlreadyRunning,
    backfill_coverage,
    run_incremental,
)
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.sync import SyncResult


def fixed_now() -> datetime:
    return datetime(2026, 7, 21, 8, 30, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path: Path) -> VectorIndexConfig:
    return replace(
        VectorIndexConfig(),
        db_path=tmp_path / "feedback.db",
        data_dir=tmp_path / "vectors",
        model_version="test-model",
    )


def _row(feedback_id: str, *, ts_ms: int = 1) -> dict[str, object]:
    return {
        "feedback_id": feedback_id,
        "conversation_id": "pending",
        "msg_seq": 0,
        "channel": "wetype",
        "ts_ms": ts_ms,
        "platform": "iOS",
        "appversion": "",
        "user_vid": "u",
        "service_vid": 1,
        "external_chat_url": None,
        "keyboard_source": "",
        "device_name": "",
        "channelid": "",
        "enginever": "",
        "msgtype": "text",
        "text": "feedback text",
        "tags": "",
        "raw_json": "{}",
        "pulled_at": 1,
    }


def _insert_feedback(conn: sqlite3.Connection, feedback_id: str) -> None:
    assert db.upsert_feedback(conn, _row(feedback_id))


def _scalar(path: Path, sql: str, params: tuple[object, ...] = ()) -> object:
    conn = db.connect(path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _successful_pull(start: datetime, end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
    _insert_feedback(conn, "feedback-1")
    db.record_feedback_source_coverage(
        conn,
        channel="wetype",
        start_ts_ms=int(start.timestamp() * 1000),
        end_ts_ms=int(end.timestamp() * 1000),
        completed_at_ms=100,
    )
    conn.commit()
    return {
        "fetched_count": 1,
        "inserted_count": 1,
        "skipped_dup_count": 0,
        "source_generation_ms": 100,
    }


def test_pipeline_commits_pull_before_vector_sync_and_persists_matching_run(config: VectorIndexConfig):
    observed: list[str] = []

    def pull_fn(start: datetime, end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
        result = _successful_pull(start, end, conn=conn)
        observed.append("pull_committed")
        return result

    def vector_fn() -> SyncResult:
        assert _scalar(config.db_path, "SELECT COUNT(*) FROM feedback") == 1
        observed.append("vector")
        return SyncResult("vector-run", 1, 0, 444)

    result = run_incremental(
        now=fixed_now(), pull_window=timedelta(minutes=30), config=config,
        pull_fn=pull_fn, vector_sync_fn=vector_fn,
    )

    assert observed == ["pull_committed", "vector"]
    assert result.status == "succeeded"
    assert result.window_start == "2026-07-21T08:00:00+00:00"
    assert result.window_end == "2026-07-21T08:30:00+00:00"
    persisted = db.connect(config.db_path)
    try:
        row = persisted.execute(
            "SELECT run_id, run_type, status, window_start_ms, window_end_ms, "
            "fetched_count, inserted_count, duplicate_count, vectorized_count, "
            "failed_count, error_code FROM embedding_sync_run WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()
    finally:
        persisted.close()
    assert tuple(row) == (
        result.run_id, "incremental_pipeline", "succeeded",
        1784620800000, 1784622600000, 1, 1, 0, 1, 0, "",
    )


def test_vector_failure_preserves_pull_returns_immutable_partial_and_is_audited(config: VectorIndexConfig):
    def failing_vector() -> SyncResult:
        raise RuntimeError("vector fixture failed")

    with pytest.raises(IncrementalPipelineError) as error:
        run_incremental(
            now=fixed_now(), pull_window=timedelta(minutes=30), config=config,
            pull_fn=_successful_pull, vector_sync_fn=failing_vector,
        )

    result = error.value.result
    assert result.pull_status == "succeeded"
    assert result.vector_status == "failed"
    assert result.status == "failed"
    assert result.error_code == "RuntimeError"
    with pytest.raises(Exception):
        result.status = "succeeded"  # type: ignore[misc]
    assert _scalar(config.db_path, "SELECT COUNT(*) FROM feedback") == 1
    assert _scalar(
        config.db_path,
        "SELECT status FROM embedding_sync_run WHERE run_id = ?",
        (result.run_id,),
    ) == "failed"


def test_pull_failure_never_starts_vector_and_records_failed_pipeline_run(config: VectorIndexConfig):
    calls: list[str] = []

    def failing_pull(start: datetime, end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
        calls.append("pull")
        raise RuntimeError("pull fixture failed")

    def vector_fn() -> SyncResult:
        calls.append("vector")
        raise AssertionError("vector must not start")

    with pytest.raises(RuntimeError, match="pull fixture failed"):
        run_incremental(
            now=fixed_now(), pull_window=timedelta(minutes=30), config=config,
            pull_fn=failing_pull, vector_sync_fn=vector_fn,
        )

    assert calls == ["pull"]
    assert _scalar(
        config.db_path,
        "SELECT status FROM embedding_sync_run WHERE run_type = 'incremental_pipeline'",
    ) == "failed"


def test_second_pipeline_invocation_fails_immediately_while_lock_is_held(config: VectorIndexConfig):
    entered = False

    def pull_fn(start: datetime, end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
        nonlocal entered
        entered = True
        with pytest.raises(PipelineAlreadyRunning):
            run_incremental(
                now=fixed_now(), pull_window=timedelta(minutes=30), config=config,
                pull_fn=_successful_pull, vector_sync_fn=lambda: SyncResult("v", 0, 0, 0),
            )
        return _successful_pull(start, end, conn=conn)

    result = run_incremental(
        now=fixed_now(), pull_window=timedelta(minutes=30), config=config,
        pull_fn=pull_fn, vector_sync_fn=lambda: SyncResult("v", 1, 0, 1),
    )

    assert entered
    assert result.status == "succeeded"


def test_repeat_overlapping_pipeline_delegates_exact_id_dedupe_to_puller(config: VectorIndexConfig):
    inserted: list[int] = []

    def pull_fn(start: datetime, end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
        added = int(db.upsert_feedback(conn, _row("stable-feedback")))
        db.record_feedback_source_coverage(
            conn, channel="wetype", start_ts_ms=int(start.timestamp() * 1000),
            end_ts_ms=int(end.timestamp() * 1000), completed_at_ms=100 + len(inserted),
        )
        conn.commit()
        inserted.append(added)
        return {"fetched_count": 1, "inserted_count": added, "skipped_dup_count": 1 - added}

    vector_fn = lambda: SyncResult("v", 0, 0, 1)
    run_incremental(now=fixed_now(), pull_window=timedelta(minutes=30), config=config, pull_fn=pull_fn, vector_sync_fn=vector_fn)
    run_incremental(now=fixed_now() + timedelta(minutes=20), pull_window=timedelta(minutes=30), config=config, pull_fn=pull_fn, vector_sync_fn=vector_fn)

    assert inserted == [1, 0]
    assert _scalar(config.db_path, "SELECT COUNT(*) FROM feedback") == 1


def test_pipeline_migrates_legacy_vector_audit_without_losing_prior_rows(config: VectorIndexConfig):
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    connection.execute(
        """CREATE TABLE embedding_sync_run (
               run_id TEXT PRIMARY KEY, run_type TEXT NOT NULL, model_version TEXT NOT NULL,
               status TEXT NOT NULL, started_at_ms INTEGER NOT NULL, finished_at_ms INTEGER
           )"""
    )
    connection.execute(
        "INSERT INTO embedding_sync_run VALUES ('old-vector', 'incremental', 'm0', 'succeeded', 1, 2)"
    )
    connection.commit()
    connection.close()

    result = run_incremental(
        now=fixed_now(), pull_window=timedelta(minutes=30), config=config,
        pull_fn=_successful_pull, vector_sync_fn=lambda: SyncResult("v", 0, 0, 1),
    )

    connection = db.connect(config.db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(embedding_sync_run)")}
        old_status = connection.execute(
            "SELECT status FROM embedding_sync_run WHERE run_id = 'old-vector'"
        ).fetchone()[0]
        pipeline_status = connection.execute(
            "SELECT status FROM embedding_sync_run WHERE run_id = ?", (result.run_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert {"window_start_ms", "window_end_ms", "fetched_count", "inserted_count", "duplicate_count", "vectorized_count", "failed_count", "error_code"} <= columns
    assert old_status == "succeeded"
    assert pipeline_status == "succeeded"


class PullSequence:
    def __init__(self, *, fail_on_call: int | None = None, channel: str = "wetype") -> None:
        self.fail_on_call = fail_on_call
        self.channel = channel
        self.calls = 0

    def __call__(self, start: datetime, end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("fixture pull failure")
        db.record_feedback_source_coverage(
            conn, channel=self.channel, start_ts_ms=int(start.timestamp() * 1000),
            end_ts_ms=int(end.timestamp() * 1000), completed_at_ms=1000 + self.calls,
        )
        return {"fetched_count": 0, "inserted_count": 0, "skipped_dup_count": 0}


def test_backfill_commits_each_six_hour_window_and_resumes_after_failure(config: VectorIndexConfig):
    start = fixed_now() - timedelta(hours=18)
    failing = PullSequence(fail_on_call=2)
    with pytest.raises(RuntimeError, match="fixture pull failure"):
        backfill_coverage(
            start=start, end=fixed_now(), chunk=timedelta(hours=6),
            config=config, pull_fn=failing,
        )

    resumed = backfill_coverage(
        start=start, end=fixed_now(), chunk=timedelta(hours=6), config=config,
        pull_fn=PullSequence(),
    )

    assert resumed.skipped_covered_windows == 1
    assert resumed.completed_windows == 2
    assert _scalar(config.db_path, "SELECT COUNT(*) FROM feedback_source_coverage") == 3


def test_backfill_skips_only_continuously_covered_exact_channel_and_uses_half_open_windows(config: VectorIndexConfig):
    start = fixed_now() - timedelta(hours=12)
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    db.record_feedback_source_coverage(
        conn, channel="wetype", start_ts_ms=int(start.timestamp() * 1000),
        end_ts_ms=int((start + timedelta(hours=3)).timestamp() * 1000), completed_at_ms=1,
    )
    db.record_feedback_source_coverage(
        conn, channel="wetype", start_ts_ms=int((start + timedelta(hours=3)).timestamp() * 1000),
        end_ts_ms=int((start + timedelta(hours=6)).timestamp() * 1000), completed_at_ms=2,
    )
    db.record_feedback_source_coverage(
        conn, channel="other", start_ts_ms=int((start + timedelta(hours=6)).timestamp() * 1000),
        end_ts_ms=int((start + timedelta(hours=12)).timestamp() * 1000), completed_at_ms=3,
    )
    conn.commit()
    conn.close()
    calls: list[tuple[datetime, datetime]] = []

    def pull_fn(window_start: datetime, window_end: datetime, *, conn: sqlite3.Connection) -> dict[str, object]:
        calls.append((window_start, window_end))
        db.record_feedback_source_coverage(
            conn, channel="wetype", start_ts_ms=int(window_start.timestamp() * 1000),
            end_ts_ms=int(window_end.timestamp() * 1000), completed_at_ms=100 + len(calls),
        )
        return {}

    result = backfill_coverage(
        start=start, end=fixed_now(), chunk=timedelta(hours=6), config=config, pull_fn=pull_fn,
    )

    assert result.skipped_covered_windows == 1
    assert result.completed_windows == 1
    assert calls == [(start + timedelta(hours=6), fixed_now())]


def test_incremental_pipeline_does_not_import_or_call_a_tagger():
    body = Path(__file__).parents[1].joinpath("incremental_pipeline.py").read_text(encoding="utf-8")
    assert "tagger" not in body
    assert "run_tagging" not in body
