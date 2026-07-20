"""Persistent state for topic-mining runs, kept outside the source database."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import TopicSpec, topic_spec_hash


RUN_STATES = frozenset({
    "pending", "running", "review_ready", "verified", "failed", "paused_quota_exhausted",
})


@dataclass(frozen=True)
class WorkerClaim:
    """Result of one persistent worker-claim attempt."""

    run_id: str
    claimed: bool
    reason: str
    status: str | None
    claim_token: str | None = None
    lease_expires_at_ms: int | None = None


class WorkerClaimLostError(RuntimeError):
    """Raised when a worker tries to publish after losing its claim."""


class TopicRunStore:
    def __init__(self, db_path: Path, artifacts_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.artifacts_dir = Path(artifacts_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
                    worker_claim_token TEXT,
                    worker_claimed_at_ms INTEGER,
                    worker_lease_expires_at_ms INTEGER,
                    UNIQUE(spec_hash, source_watermark_ms)
                )"""
            )
            existing = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(topic_run)")
            }
            for name, sql_type in (
                ("worker_claim_token", "TEXT"),
                ("worker_claimed_at_ms", "INTEGER"),
                ("worker_lease_expires_at_ms", "INTEGER"),
            ):
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE topic_run ADD COLUMN {name} {sql_type}"
                    )

    def create_or_get(self, spec: TopicSpec, source_watermark_ms: int) -> dict[str, Any]:
        spec_hash = topic_spec_hash(spec)
        run_id = hashlib.sha256(f"{spec_hash}:{source_watermark_ms}".encode("utf-8")).hexdigest()[:16]
        artifact_dir = self.artifacts_dir / run_id
        # An artifact directory is part of a usable run.  Create (or repair) it
        # before publishing the database row, so workers never see a run that
        # cannot write its isolated outputs.
        artifact_dir.mkdir(parents=True, exist_ok=True)
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
            "worker_claim_token": None,
            "worker_claimed_at_ms": None,
            "worker_lease_expires_at_ms": None,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT INTO topic_run (
                    run_id, spec_hash, spec_json, source_watermark_ms, status, stage,
                    error_code, error_message, artifact_dir, created_at_ms, updated_at_ms, manifest_json,
                    worker_claim_token, worker_claimed_at_ms, worker_lease_expires_at_ms
                ) VALUES (
                    :run_id, :spec_hash, :spec_json, :source_watermark_ms, :status, :stage,
                    :error_code, :error_message, :artifact_dir, :created_at_ms, :updated_at_ms, :manifest_json,
                    :worker_claim_token, :worker_claimed_at_ms, :worker_lease_expires_at_ms
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
        result = dict(row)
        result["created"] = created
        return result

    def claim_worker(
        self,
        run_id: str,
        *,
        lease_seconds: int,
        now_ms: int | None = None,
    ) -> WorkerClaim:
        """Atomically claim one recoverable run under a backend-owned lease.

        A ``running`` row without a lease is a legacy/orphaned worker.  A live
        lease blocks a second worker until its backend-controlled expiry.
        """
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("worker lease seconds must be positive")
        claimed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        lease_expires_at_ms = claimed_at_ms + lease_seconds * 1000
        claim_token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, worker_lease_expires_at_ms FROM topic_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return WorkerClaim(run_id, False, "run_not_found", None)
            status = str(row["status"])
            if status in {"review_ready", "verified"}:
                return WorkerClaim(run_id, False, "run_not_recoverable", status)
            live_lease = row["worker_lease_expires_at_ms"]
            if status == "running" and live_lease is not None and int(live_lease) > claimed_at_ms:
                return WorkerClaim(
                    run_id, False, "worker_already_claimed", status,
                    lease_expires_at_ms=int(live_lease),
                )
            if status not in {"pending", "running", "paused_quota_exhausted", "failed"}:
                return WorkerClaim(run_id, False, "run_not_recoverable", status)
            cursor = connection.execute(
                """UPDATE topic_run
                   SET status = 'running', error_code = NULL, error_message = NULL,
                       worker_claim_token = ?, worker_claimed_at_ms = ?,
                       worker_lease_expires_at_ms = ?, updated_at_ms = ?
                   WHERE run_id = ?""",
                (
                    claim_token, claimed_at_ms, lease_expires_at_ms,
                    claimed_at_ms, run_id,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - guarded by write lock
                raise RuntimeError("topic run disappeared during worker claim")
        return WorkerClaim(
            run_id, True, "claimed", "running", claim_token,
            lease_expires_at_ms,
        )

    def renew_worker_claim(
        self,
        run_id: str,
        claim_token: str,
        *,
        lease_seconds: int,
        now_ms: int | None = None,
    ) -> bool:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("worker lease seconds must be positive")
        renewed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE topic_run
                   SET worker_lease_expires_at_ms = ?, updated_at_ms = ?
                   WHERE run_id = ? AND status = 'running'
                     AND worker_claim_token = ?
                     AND worker_lease_expires_at_ms > ?""",
                (
                    renewed_at_ms + lease_seconds * 1000,
                    renewed_at_ms,
                    run_id,
                    claim_token,
                    renewed_at_ms,
                ),
            )
        return cursor.rowcount == 1

    def release_worker_claim(self, run_id: str, claim_token: str) -> bool:
        """Release only the lease still owned by this worker token."""
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE topic_run
                   SET worker_claim_token = NULL, worker_claimed_at_ms = NULL,
                       worker_lease_expires_at_ms = NULL
                   WHERE run_id = ? AND worker_claim_token = ?""",
                (run_id, claim_token),
            )
        return cursor.rowcount == 1

    def for_worker_claim(
        self,
        claim_token: str,
        *,
        cancellation_event: Any | None = None,
    ) -> "ClaimedTopicRunStore":
        return ClaimedTopicRunStore(
            self, claim_token, cancellation_event=cancellation_event,
        )

    def fail_worker_claim(
        self,
        run_id: str,
        claim_token: str,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Atomically fail and release only the worker that still owns a run."""
        now_ms = int(time.time() * 1000)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE topic_run
                   SET status = 'failed', error_code = ?, error_message = ?,
                       updated_at_ms = ?, worker_claim_token = NULL,
                       worker_claimed_at_ms = NULL,
                       worker_lease_expires_at_ms = NULL
                   WHERE run_id = ? AND status = 'running'
                     AND worker_claim_token = ?
                     AND worker_lease_expires_at_ms > ?""",
                (
                    error_code, error_message, now_ms, run_id, claim_token,
                    now_ms,
                ),
            )
        return cursor.rowcount == 1

    def update_status(
        self,
        run_id: str,
        status: str,
        *,
        stage: str | None = None,
        worker_claim_token: str | None = None,
    ) -> None:
        if status not in RUN_STATES:
            raise ValueError(f"unsupported topic run status: {status}")
        now_ms = int(time.time() * 1000)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE topic_run
                   SET status = ?, stage = COALESCE(?, stage), updated_at_ms = ?,
                       worker_claim_token = CASE WHEN ? = 'running' THEN worker_claim_token ELSE NULL END,
                       worker_claimed_at_ms = CASE WHEN ? = 'running' THEN worker_claimed_at_ms ELSE NULL END,
                       worker_lease_expires_at_ms = CASE WHEN ? = 'running' THEN worker_lease_expires_at_ms ELSE NULL END
                   WHERE run_id = ? AND (
                       (worker_claim_token IS NULL AND ? IS NULL)
                       OR (worker_claim_token = ? AND worker_lease_expires_at_ms > ?)
                   )""",
                (
                    status, stage, now_ms, status, status, status, run_id,
                    worker_claim_token, worker_claim_token, now_ms,
                ),
            )
        if cursor.rowcount != 1:
            self._raise_missing_or_lost(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return the isolated run row without exposing source databases."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM topic_run WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def update_manifest(
        self,
        run_id: str,
        manifest: Mapping[str, Any],
        *,
        stage: str,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        worker_claim_token: str | None = None,
    ) -> None:
        """Atomically publish a stage manifest and optional stable status."""
        if status is not None and status not in RUN_STATES:
            raise ValueError(f"unsupported topic run status: {status}")
        now_ms = int(time.time() * 1000)
        with self._connect() as connection:
            worker_reset = status is not None and status != "running"
            cursor = connection.execute(
                """UPDATE topic_run SET stage = ?, status = COALESCE(?, status),
                   error_code = ?, error_message = ?, manifest_json = ?, updated_at_ms = ?,
                   worker_claim_token = CASE WHEN ? THEN NULL ELSE worker_claim_token END,
                   worker_claimed_at_ms = CASE WHEN ? THEN NULL ELSE worker_claimed_at_ms END,
                   worker_lease_expires_at_ms = CASE WHEN ? THEN NULL ELSE worker_lease_expires_at_ms END
                   WHERE run_id = ? AND (
                       (worker_claim_token IS NULL AND ? IS NULL)
                       OR (worker_claim_token = ? AND worker_lease_expires_at_ms > ?)
                   )""",
                (
                    stage, status, error_code, error_message,
                    json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    now_ms, worker_reset, worker_reset, worker_reset, run_id,
                    worker_claim_token, worker_claim_token, now_ms,
                ),
            )
        if cursor.rowcount != 1:
            self._raise_missing_or_lost(run_id)

    def assert_worker_claim(
        self,
        run_id: str,
        claim_token: str,
        *,
        now_ms: int | None = None,
    ) -> None:
        checked_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM topic_run
                   WHERE run_id = ? AND status = 'running'
                     AND worker_claim_token = ?
                     AND worker_lease_expires_at_ms > ?""",
                (run_id, claim_token, checked_at_ms),
            ).fetchone()
        if row is None:
            self._raise_missing_or_lost(run_id)

    def publish_worker_files(
        self,
        run_id: str,
        claim_token: str,
        files: list[tuple[Path, Path]],
        *,
        now_ms: int | None = None,
    ) -> None:
        """Snapshot staged files while serializing publication against reclaim."""
        checked_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT artifact_dir, status, worker_claim_token,
                          worker_lease_expires_at_ms
                   FROM topic_run WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if (
                row["status"] != "running"
                or row["worker_claim_token"] != claim_token
                or row["worker_lease_expires_at_ms"] is None
                or int(row["worker_lease_expires_at_ms"]) <= checked_at_ms
            ):
                raise WorkerClaimLostError("worker_claim_lost")
            artifact_dir = Path(str(row["artifact_dir"])).resolve()
        token_hash = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()[:16]
        claim_root = (artifact_dir / ".worker_claims" / token_hash).resolve()
        normalized: list[tuple[Path, Path]] = []
        for source, target in files:
            source_path = Path(source).resolve()
            target_path = Path(target).resolve()
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            try:
                source_path.relative_to(claim_root)
            except ValueError as exc:
                raise ValueError("worker artifact source must belong to its claim") from exc
            try:
                relative_target = target_path.relative_to(artifact_dir)
            except ValueError as exc:
                raise ValueError("worker artifact target must stay in its run") from exc
            if not relative_target.parts or relative_target.parts[0] == ".worker_claims":
                raise ValueError("worker artifact target is reserved")
            normalized.append((source_path, target_path))

        # Slow copies happen before the publication transaction. Re-read the
        # clock and claim immediately before the quick rename phase.
        prepared: list[tuple[Path, Path]] = []
        try:
            for source_path, target_path in normalized:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = target_path.with_name(
                    f".{target_path.name}.{token_hash}.{secrets.token_hex(8)}.tmp"
                )
                prepared.append((temporary, target_path))
                with source_path.open("rb") as source_file, temporary.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
                    target_file.flush()
                    os.fsync(target_file.fileno())

            promotion_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    """SELECT status, worker_claim_token,
                              worker_lease_expires_at_ms
                       FROM topic_run WHERE run_id = ?""",
                    (run_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(run_id)
                if (
                    current["status"] != "running"
                    or current["worker_claim_token"] != claim_token
                    or current["worker_lease_expires_at_ms"] is None
                    or int(current["worker_lease_expires_at_ms"]) <= promotion_at_ms
                ):
                    raise WorkerClaimLostError("worker_claim_lost")
                for temporary, target_path in prepared:
                    os.replace(temporary, target_path)
        finally:
            for temporary, _target_path in prepared:
                temporary.unlink(missing_ok=True)

    def _raise_missing_or_lost(self, run_id: str) -> None:
        if self.get(run_id) is None:
            raise KeyError(run_id)
        raise WorkerClaimLostError("worker_claim_lost")


class ClaimedTopicRunStore:
    """Worker-scoped view that fences every durable pipeline publication."""

    def __init__(
        self,
        store: TopicRunStore,
        claim_token: str,
        *,
        cancellation_event: Any | None = None,
    ) -> None:
        self._store = store
        self._claim_token = claim_token
        self._cancellation_event = cancellation_event

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._store.get(run_id)

    def ensure_worker_claim(self, run_id: str) -> None:
        if self._cancellation_event is not None and self._cancellation_event.is_set():
            raise WorkerClaimLostError("worker_claim_lost")
        self._store.assert_worker_claim(run_id, self._claim_token)

    def stage_artifact_dir(self, artifact_dir: Path, stage: str) -> Path:
        token_hash = hashlib.sha256(self._claim_token.encode("utf-8")).hexdigest()[:16]
        path = Path(artifact_dir) / ".worker_claims" / token_hash / stage
        path.mkdir(parents=True, exist_ok=True)
        return path

    def publish_worker_files(
        self,
        run_id: str,
        files: list[tuple[Path, Path]],
    ) -> None:
        self.ensure_worker_claim(run_id)
        self._store.publish_worker_files(run_id, self._claim_token, files)

    def update_status(
        self, run_id: str, status: str, *, stage: str | None = None,
    ) -> None:
        self.ensure_worker_claim(run_id)
        self._store.update_status(
            run_id, status, stage=stage,
            worker_claim_token=self._claim_token,
        )

    def update_manifest(
        self,
        run_id: str,
        manifest: Mapping[str, Any],
        *,
        stage: str,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.ensure_worker_claim(run_id)
        self._store.update_manifest(
            run_id,
            manifest,
            stage=stage,
            status=status,
            error_code=error_code,
            error_message=error_message,
            worker_claim_token=self._claim_token,
        )
