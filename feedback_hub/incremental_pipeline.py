"""Locked pull-then-vector orchestration for feedback ingestion.

Lock order is deliberately one-way: this module takes ``.pipeline.lock``
first, commits source ingestion while briefly holding the vector module's
``.source-ingestion.lock``, releases the SQLite connection, and only then
calls ``sync_pending`` (which takes ``.sync.lock``).  It never holds the
source lock while asking for the sync lock, so it cannot invert the vector
sync lock order.
"""
from __future__ import annotations

import fcntl
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from feedback_hub import db, puller
from feedback_hub.config import DEFAULT_CHANNEL
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.sync import (
    SyncResult,
    source_ingestion_lock,
    sync_pending,
)


_LOCAL_LOCKS: set[Path] = set()
_LOCAL_LOCKS_GUARD = threading.Lock()


class PipelineAlreadyRunning(RuntimeError):
    """Raised when another incremental pipeline owns the non-blocking lock."""


@dataclass(frozen=True)
class IncrementalRunResult:
    run_id: str
    status: str
    started_at: str
    finished_at: str
    window_start: str
    window_end: str
    pull_status: str
    vector_status: str
    fetched_count: int
    inserted_count: int
    skipped_dup_count: int
    vectorized_count: int
    source_generation_ms: int | None
    vector_watermark_ms: int | None
    error_code: str


class IncrementalPipelineError(RuntimeError):
    """A vector failure with its immutable, already-persisted partial result."""

    def __init__(self, result: IncrementalRunResult) -> None:
        self.result = result
        super().__init__(
            f"incremental pipeline vector stage failed: {result.error_code or 'unknown'}"
        )


@dataclass(frozen=True)
class BackfillResult:
    start: str
    end: str
    chunk: str
    completed_windows: int
    skipped_covered_windows: int


@contextmanager
def nonblocking_lock(path: Path) -> Iterator[None]:
    """Take a process- and host-wide non-blocking pipeline lock."""
    resolved = path.absolute()
    with _LOCAL_LOCKS_GUARD:
        if resolved in _LOCAL_LOCKS:
            raise PipelineAlreadyRunning("incremental pipeline already running")
        _LOCAL_LOCKS.add(resolved)
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PipelineAlreadyRunning("incremental pipeline already running") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if handle is not None:
            handle.close()
        with _LOCAL_LOCKS_GUARD:
            _LOCAL_LOCKS.discard(resolved)


def _as_utc(value: datetime) -> datetime:
    """Make naive fixture datetimes deterministic and preserve aware instants."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp_ms(value: datetime) -> int:
    return int(_as_utc(value).timestamp() * 1000)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _table_columns(connection: Any, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"] if hasattr(row, "keys") else row[1]) for row in rows}


def _ensure_pipeline_audit_columns(connection: Any) -> None:
    """Add only missing audit fields, preserving all prior vector-sync rows."""
    additions = {
        "window_start_ms": "INTEGER",
        "window_end_ms": "INTEGER",
        "fetched_count": "INTEGER NOT NULL DEFAULT 0",
        "inserted_count": "INTEGER NOT NULL DEFAULT 0",
        "duplicate_count": "INTEGER NOT NULL DEFAULT 0",
        "vectorized_count": "INTEGER NOT NULL DEFAULT 0",
        "failed_count": "INTEGER NOT NULL DEFAULT 0",
        "error_code": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in _table_columns(connection, "embedding_sync_run"):
            connection.execute(f"ALTER TABLE embedding_sync_run ADD COLUMN {name} {ddl}")
    connection.commit()


def _insert_pipeline_run(
    connection: Any, *, run_id: str, config: VectorIndexConfig,
    started_at_ms: int, window_start_ms: int, window_end_ms: int,
) -> None:
    connection.execute(
        """INSERT INTO embedding_sync_run
           (run_id, run_type, model_version, status, started_at_ms,
            window_start_ms, window_end_ms)
           VALUES (?, 'incremental_pipeline', ?, 'running', ?, ?, ?)""",
        (run_id, config.model_version, started_at_ms, window_start_ms, window_end_ms),
    )
    connection.commit()


def _finish_pipeline_run(connection: Any, result: IncrementalRunResult) -> None:
    connection.execute(
        """UPDATE embedding_sync_run
           SET status = ?, finished_at_ms = ?, window_start_ms = ?, window_end_ms = ?,
               fetched_count = ?, inserted_count = ?, duplicate_count = ?,
               vectorized_count = ?, failed_count = ?, error_code = ?
           WHERE run_id = ?""",
        (
            result.status, _timestamp_ms(datetime.fromisoformat(result.finished_at)),
            _timestamp_ms(datetime.fromisoformat(result.window_start)),
            _timestamp_ms(datetime.fromisoformat(result.window_end)),
            result.fetched_count, result.inserted_count, result.skipped_dup_count,
            result.vectorized_count, 1 if result.status == "failed" else 0,
            result.error_code, result.run_id,
        ),
    )
    connection.commit()


def _result(
    *, run_id: str, started_at: datetime, window_start: datetime, window_end: datetime,
    pull_status: str, vector_status: str, fetched_count: int = 0,
    inserted_count: int = 0, skipped_dup_count: int = 0, vectorized_count: int = 0,
    source_generation_ms: int | None = None, vector_watermark_ms: int | None = None,
    error_code: str = "",
) -> IncrementalRunResult:
    if pull_status == "failed" or vector_status == "failed":
        status = "failed"
    elif vector_status == "partial":
        status = "partial"
    else:
        status = "succeeded"
    return IncrementalRunResult(
        run_id=run_id, status=status, started_at=_as_utc(started_at).isoformat(),
        finished_at=_utc_now().isoformat(), window_start=_as_utc(window_start).isoformat(),
        window_end=_as_utc(window_end).isoformat(), pull_status=pull_status,
        vector_status=vector_status, fetched_count=fetched_count,
        inserted_count=inserted_count, skipped_dup_count=skipped_dup_count,
        vectorized_count=vectorized_count, source_generation_ms=source_generation_ms,
        vector_watermark_ms=vector_watermark_ms, error_code=error_code,
    )


def _pull_counts(payload: dict[str, Any]) -> tuple[int, int, int, int | None]:
    return (
        int(payload.get("fetched_count", 0)), int(payload.get("inserted_count", 0)),
        int(payload.get("skipped_dup_count", 0)),
        int(payload["source_generation_ms"])
        if payload.get("source_generation_ms") is not None else None,
    )


def run_incremental(
    *, now: datetime, pull_window: timedelta, pull_fn: Callable[..., dict[str, Any]] | None = None,
    vector_sync_fn: Callable[[], SyncResult] | None = None,
    config: VectorIndexConfig | None = None,
) -> IncrementalRunResult:
    """Commit one supplied pull window before synchronizing missing vectors."""
    if pull_window <= timedelta(0):
        raise ValueError("pull_window must be positive")
    active_config = config or VectorIndexConfig()
    window_end = _as_utc(now)
    window_start = window_end - pull_window
    run_id = uuid.uuid4().hex
    started_at = _utc_now()
    pull = pull_fn or puller.pull
    vector_sync = vector_sync_fn or (lambda: sync_pending(active_config))

    with nonblocking_lock(active_config.data_dir / ".pipeline.lock"):
        connection = db.connect(active_config.db_path)
        try:
            db.init_schema(connection)
            from feedback_hub.vector_index.repository import VectorRepository

            repository = VectorRepository(connection)
            repository.init_schema()
            _ensure_pipeline_audit_columns(connection)
            _insert_pipeline_run(
                connection, run_id=run_id, config=active_config,
                started_at_ms=_timestamp_ms(started_at),
                window_start_ms=_timestamp_ms(window_start), window_end_ms=_timestamp_ms(window_end),
            )
            try:
                with source_ingestion_lock(active_config):
                    pull_payload = pull(window_start, window_end, conn=connection)
                    connection.commit()
            except Exception as exc:
                connection.rollback()
                failed = _result(
                    run_id=run_id, started_at=started_at, window_start=window_start,
                    window_end=window_end, pull_status="failed", vector_status="not_started",
                    error_code=type(exc).__name__,
                )
                _finish_pipeline_run(connection, failed)
                raise
            fetched, inserted, duplicates, generation = _pull_counts(pull_payload)
        finally:
            connection.close()

        # The source connection has closed: vector sync observes only committed raw rows.
        try:
            synced = vector_sync()
        except Exception as exc:
            failed = _result(
                run_id=run_id, started_at=started_at, window_start=window_start,
                window_end=window_end, pull_status="succeeded", vector_status="failed",
                fetched_count=fetched, inserted_count=inserted, skipped_dup_count=duplicates,
                source_generation_ms=generation, error_code=type(exc).__name__,
            )
            audit_connection = db.connect(active_config.db_path)
            try:
                _finish_pipeline_run(audit_connection, failed)
            finally:
                audit_connection.close()
            raise IncrementalPipelineError(failed) from exc

        vector_status = "succeeded" if synced.pending_count == 0 else "partial"
        succeeded = _result(
            run_id=run_id, started_at=started_at, window_start=window_start,
            window_end=window_end, pull_status="succeeded", vector_status=vector_status,
            fetched_count=fetched, inserted_count=inserted, skipped_dup_count=duplicates,
            vectorized_count=int(synced.vectorized_count), source_generation_ms=generation,
            vector_watermark_ms=int(synced.watermark_ts_ms),
        )
        audit_connection = db.connect(active_config.db_path)
        try:
            _finish_pipeline_run(audit_connection, succeeded)
        finally:
            audit_connection.close()
        return succeeded


def _covered_continuously(
    connection: Any, *, channel: str, start_ms: int, end_ms: int,
) -> bool:
    rows = connection.execute(
        """SELECT start_ts_ms, end_ts_ms FROM feedback_source_coverage
           WHERE channel = ? AND start_ts_ms < ? AND end_ts_ms > ?
           ORDER BY start_ts_ms, end_ts_ms""",
        (channel, end_ms, start_ms),
    ).fetchall()
    cursor = start_ms
    for row in rows:
        row_start, row_end = int(row[0]), int(row[1])
        if row_start > cursor:
            return False
        cursor = max(cursor, row_end)
        if cursor >= end_ms:
            return True
    return False


def backfill_coverage(
    *, start: datetime, end: datetime, chunk: timedelta | None = None,
    pull_fn: Callable[..., dict[str, Any]] | None = None,
    config: VectorIndexConfig | None = None, channel: str = DEFAULT_CHANNEL,
) -> BackfillResult:
    """Fill missing half-open source windows, checkpointing every successful chunk."""
    active_config = config or VectorIndexConfig()
    interval = chunk or timedelta(hours=6)
    if interval <= timedelta(0):
        raise ValueError("chunk must be positive")
    cursor = _as_utc(start)
    end_at = _as_utc(end)
    if end_at <= cursor:
        raise ValueError("end must be after start")
    pull = pull_fn or puller.pull
    completed = 0
    skipped = 0

    with nonblocking_lock(active_config.data_dir / ".pipeline.lock"):
        while cursor < end_at:
            window_end = min(cursor + interval, end_at)
            connection = db.connect(active_config.db_path)
            try:
                db.init_schema(connection)
                if _covered_continuously(
                    connection, channel=channel, start_ms=_timestamp_ms(cursor),
                    end_ms=_timestamp_ms(window_end),
                ):
                    skipped += 1
                else:
                    with source_ingestion_lock(active_config):
                        pull(cursor, window_end, conn=connection)
                        connection.commit()
                    completed += 1
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            cursor = window_end

    return BackfillResult(
        start=_as_utc(start).isoformat(), end=end_at.isoformat(), chunk=str(interval),
        completed_windows=completed, skipped_covered_windows=skipped,
    )
