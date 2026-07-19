"""Persistent state for topic-mining runs, kept outside the source database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .contracts import TopicSpec, topic_spec_hash


RUN_STATES = frozenset({
    "pending", "running", "review_ready", "verified", "failed", "paused_quota_exhausted",
})


class TopicRunStore:
    def __init__(self, db_path: Path, artifacts_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.artifacts_dir = Path(artifacts_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS topic_run (
                    run_id TEXT PRIMARY KEY,
                    spec_hash TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    source_watermark_ms INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'running', 'review_ready', 'verified', 'failed',
                        'paused_quota_exhausted'
                    )),
                    stage TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    artifact_dir TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    UNIQUE(spec_hash, source_watermark_ms)
                )"""
            )

    def create_or_get(self, spec: TopicSpec, source_watermark_ms: int) -> dict[str, Any]:
        spec_hash = topic_spec_hash(spec)
        run_id = hashlib.sha256(f"{spec_hash}:{source_watermark_ms}".encode("utf-8")).hexdigest()[:16]
        artifact_dir = self.artifacts_dir / run_id
        now_ms = int(time.time() * 1000)
        record = {
            "run_id": run_id,
            "spec_hash": spec_hash,
            "spec_json": json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "source_watermark_ms": int(source_watermark_ms),
            "status": "pending",
            "stage": "created",
            "error_code": None,
            "error_message": None,
            "artifact_dir": str(artifact_dir),
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "manifest_json": "{}",
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT INTO topic_run (
                    run_id, spec_hash, spec_json, source_watermark_ms, status, stage,
                    error_code, error_message, artifact_dir, created_at_ms, updated_at_ms, manifest_json
                ) VALUES (
                    :run_id, :spec_hash, :spec_json, :source_watermark_ms, :status, :stage,
                    :error_code, :error_message, :artifact_dir, :created_at_ms, :updated_at_ms, :manifest_json
                ) ON CONFLICT(spec_hash, source_watermark_ms) DO NOTHING""",
                record,
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM topic_run WHERE spec_hash = ? AND source_watermark_ms = ?",
                (spec_hash, int(source_watermark_ms)),
            ).fetchone()
        if row is None:  # pragma: no cover - defensive safeguard for damaged stores
            raise RuntimeError("topic run disappeared during creation")
        if created:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        result = dict(row)
        result["created"] = created
        return result

    def update_status(self, run_id: str, status: str, *, stage: str | None = None) -> None:
        if status not in RUN_STATES:
            raise ValueError(f"unsupported topic run status: {status}")
        now_ms = int(time.time() * 1000)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE topic_run SET status = ?, stage = COALESCE(?, stage), updated_at_ms = ? WHERE run_id = ?",
                (status, stage, now_ms, run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(run_id)
