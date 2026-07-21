"""SQLite repository for vector shard and embedding metadata."""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Sequence

from feedback_hub import db

from .config import VectorIndexConfig
from .models import EmbeddingRecord, PendingFeedback, ShardMetadata, VectorIndexStatus


def now_ms() -> int:
    return int(time.time() * 1000)


class VectorRepository:
    """Read pending feedback and transactionally register published shards."""

    def __init__(
        self,
        config_or_connection: VectorIndexConfig | sqlite3.Connection | None = None,
    ) -> None:
        if isinstance(config_or_connection, sqlite3.Connection):
            self.connection = config_or_connection
            self._owns_connection = False
        else:
            self.config = config_or_connection or VectorIndexConfig()
            self.connection = db.connect(self.config.db_path)
            self._owns_connection = True

    def close(self) -> None:
        """Close the connection only when this repository created it."""
        if self._owns_connection:
            self.connection.close()

    def init_schema(self) -> None:
        """Create the vector metadata tables and indexes idempotently."""
        schema_path = Path(__file__).with_name("schema.sql")
        self.connection.executescript(schema_path.read_text(encoding="utf-8"))
        self.connection.commit()

    def pending_feedback(self, model_version: str, limit: int) -> list[PendingFeedback]:
        rows = self.connection.execute(
            """SELECT f.feedback_id, f.conversation_id, f.ts_ms, f.platform, f.channel,
                      f.appversion, f.text
               FROM feedback f
               WHERE TRIM(f.text) <> '' AND NOT EXISTS (
                   SELECT 1 FROM embedding_record er
                   WHERE er.feedback_id = f.feedback_id AND er.model_version = ?
               )
               ORDER BY f.ts_ms, f.feedback_id LIMIT ?""",
            (model_version, limit),
        ).fetchall()
        return [PendingFeedback.from_row(row) for row in rows]

    def pending_count(self, model_version: str) -> int:
        """Return the number of nonblank feedback rows missing this model version."""
        row = self.connection.execute(
            """SELECT COUNT(*)
               FROM feedback f
               WHERE TRIM(f.text) <> '' AND NOT EXISTS (
                   SELECT 1 FROM embedding_record er
                   WHERE er.feedback_id = f.feedback_id AND er.model_version = ?
               )""",
            (model_version,),
        ).fetchone()
        return int(row[0])

    def source_watermark_ms(self) -> int:
        """Return the latest successful source coverage watermark, or zero."""
        row = self.connection.execute(
            "SELECT COALESCE(MAX(completed_at_ms), 0) FROM feedback_source_coverage"
        ).fetchone()
        return int(row[0])

    def pending_and_coverage_snapshot(self, model_version: str) -> tuple[int, int]:
        """Read pending work and source coverage in one SQLite write snapshot.

        Ingestion must commit feedback rows and ``feedback_source_coverage`` in
        its own SQLite transaction.  ``BEGIN IMMEDIATE`` serializes this final
        read with that writer, preventing a pending/coverage TOCTOU decision.
        """
        if self.connection.in_transaction:
            raise RuntimeError("source snapshot requires no caller-owned transaction")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            pending = self.pending_count(model_version)
            coverage = self.source_watermark_ms()
            self.connection.commit()
            return pending, coverage
        except Exception:
            self.connection.rollback()
            raise

    def mark_publication(
        self, *, publication_key: str, model_version: str, generation: int,
        watermark_ts_ms: int,
    ) -> None:
        """Write the SQLite commit witness inside a coordinated publication."""
        self.connection.execute(
            """INSERT INTO embedding_publication_marker
               (publication_key, model_version, generation, watermark_ts_ms, updated_at_ms)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(publication_key) DO UPDATE SET
                 model_version = excluded.model_version,
                 generation = excluded.generation,
                 watermark_ts_ms = excluded.watermark_ts_ms,
                 updated_at_ms = excluded.updated_at_ms""",
            (publication_key, model_version, generation, watermark_ts_ms, now_ms()),
        )

    def publication_matches(
        self, *, publication_key: str, model_version: str, generation: int,
        watermark_ts_ms: int,
    ) -> bool:
        row = self.connection.execute(
            """SELECT model_version, generation, watermark_ts_ms
               FROM embedding_publication_marker WHERE publication_key = ?""",
            (publication_key,),
        ).fetchone()
        return row is not None and tuple(row) == (model_version, generation, watermark_ts_ms)

    def begin_run(self, *, run_type: str, model_version: str) -> str:
        run_id = uuid.uuid4().hex
        self.connection.execute(
            """INSERT INTO embedding_sync_run
               (run_id, run_type, model_version, status, started_at_ms)
               VALUES (?, ?, ?, 'running', ?)""",
            (run_id, run_type, model_version, now_ms()),
        )
        self.connection.commit()
        return run_id

    def _insert_shard(self, shard: ShardMetadata) -> None:
        self.connection.execute(
            """INSERT INTO embedding_shard
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (
                shard.shard_id,
                shard.model_version,
                shard.path,
                shard.dimension,
                shard.row_count,
                shard.checksum,
                now_ms(),
            ),
        )

    def _insert_record(self, record: EmbeddingRecord) -> None:
        self.connection.execute(
            "INSERT INTO embedding_record VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.feedback_id,
                record.model_version,
                record.content_hash,
                record.shard_id,
                record.row_offset,
                record.embedded_at_ms,
            ),
        )

    def publish_shard(
        self, shard: ShardMetadata, records: Sequence[EmbeddingRecord]
    ) -> None:
        """Publish one shard atomically without taking ownership of caller transactions."""
        savepoint = f"publish_shard_{uuid.uuid4().hex}"
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            self._insert_shard(shard)
            for record in records:
                self._insert_record(record)
        except Exception:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        vectorized_count: int,
        error_code: str = "",
    ) -> None:
        self.connection.execute(
            """UPDATE embedding_sync_run
               SET status = ?, vectorized_count = ?, error_code = ?, finished_at_ms = ?
               WHERE run_id = ?""",
            (status, vectorized_count, error_code, now_ms(), run_id),
        )
        self.connection.commit()

    def status(self) -> VectorIndexStatus:
        rows = self.connection.execute(
            "SELECT state, COUNT(*) FROM embedding_shard GROUP BY state"
        ).fetchall()
        counts = {str(row[0]): int(row[1]) for row in rows}
        return VectorIndexStatus(
            active_shards=counts.get("active", 0),
            retired_shards=counts.get("retired", 0),
        )
