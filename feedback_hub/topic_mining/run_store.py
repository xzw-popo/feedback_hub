"""Persistent state for topic-mining runs, kept outside the source database."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
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


class RunPublicationConflictError(RuntimeError):
    """Raised when a terminal mutation was based on stale run state."""


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
                    publication_json TEXT,
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
                ("publication_json", "TEXT"),
            ):
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE topic_run ADD COLUMN {name} {sql_type}"
                    )

    def create_or_get(
        self,
        spec: TopicSpec,
        source_watermark_ms: int,
        *,
        initial_files: Mapping[str, Path] | None = None,
        initial_manifest: Mapping[str, Any] | None = None,
        initial_stage: str = "created",
    ) -> dict[str, Any]:
        """Create one run, optionally installing a frozen snapshot first."""
        if (initial_files is None) != (initial_manifest is None):
            raise ValueError("initial files and manifest must be provided together")
        if not isinstance(initial_stage, str) or not initial_stage:
            raise ValueError("initial stage is required")
        spec_hash = topic_spec_hash(spec)
        run_id = hashlib.sha256(f"{spec_hash}:{source_watermark_ms}".encode("utf-8")).hexdigest()[:16]
        artifact_dir = self.artifacts_dir / run_id
        # An artifact directory is part of a usable run.  Create (or repair) it
        # before publishing the database row, so workers never see a run that
        # cannot write its isolated outputs.
        if initial_files is None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        now_ms = int(time.time() * 1000)
        manifest_json = json.dumps(
            dict(initial_manifest or {}), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        record = {
            "run_id": run_id,
            "spec_hash": spec_hash,
            "spec_json": json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "source_watermark_ms": int(source_watermark_ms),
            "status": "pending",
            "stage": initial_stage,
            "error_code": None,
            "error_message": None,
            "artifact_dir": str(artifact_dir),
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "manifest_json": manifest_json,
            "worker_claim_token": None,
            "worker_claimed_at_ms": None,
            "worker_lease_expires_at_ms": None,
            "publication_json": None,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM topic_run WHERE spec_hash = ? AND source_watermark_ms = ?",
                (spec_hash, int(source_watermark_ms)),
            ).fetchone()
            if row is not None:
                artifact_dir.mkdir(parents=True, exist_ok=True)
                result = dict(row)
                result["created"] = False
                return result
            if initial_files is not None:
                artifact_dir.mkdir(parents=True, exist_ok=True)
                self._fsync_directory(self.artifacts_dir)
                artifacts = initial_manifest.get("artifacts")
                if not isinstance(artifacts, Mapping):
                    raise ValueError("initial manifest artifacts must be an object")
                for name, source in initial_files.items():
                    if not self._terminal_name_safe(name):
                        raise ValueError("invalid initial artifact name")
                    source_path = Path(source)
                    expected = artifacts.get(name)
                    if (
                        not source_path.is_file()
                        or not isinstance(expected, str)
                        or self._sha256(source_path) != expected
                    ):
                        raise ValueError("initial artifact hash mismatch")
                    target = artifact_dir / name
                    temporary = target.with_name(
                        f".{target.name}.{secrets.token_hex(8)}.tmp"
                    )
                    try:
                        with source_path.open("rb") as source_handle, temporary.open("wb") as target_handle:
                            while chunk := source_handle.read(1024 * 1024):
                                target_handle.write(chunk)
                            target_handle.flush()
                            os.fsync(target_handle.fileno())
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                self._fsync_directory(artifact_dir)
                self._write_manifest_mirror(artifact_dir, initial_manifest)
            cursor = connection.execute(
                """INSERT INTO topic_run (
                    run_id, spec_hash, spec_json, source_watermark_ms, status, stage,
                    error_code, error_message, artifact_dir, created_at_ms, updated_at_ms, manifest_json,
                    worker_claim_token, worker_claimed_at_ms, worker_lease_expires_at_ms,
                    publication_json
                ) VALUES (
                    :run_id, :spec_hash, :spec_json, :source_watermark_ms, :status, :stage,
                    :error_code, :error_message, :artifact_dir, :created_at_ms, :updated_at_ms, :manifest_json,
                    :worker_claim_token, :worker_claimed_at_ms, :worker_lease_expires_at_ms,
                    :publication_json
                )""",
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
        claim_token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            claimed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
            lease_expires_at_ms = claimed_at_ms + lease_seconds * 1000
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            renewed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_ms = int(time.time() * 1000)
            cursor = connection.execute(
                """UPDATE topic_run
                   SET status = 'failed', error_code = ?, error_message = ?,
                       updated_at_ms = ?, worker_claim_token = NULL,
                       worker_claimed_at_ms = NULL,
                       worker_lease_expires_at_ms = NULL
                   WHERE run_id = ? AND status = 'running'
                     AND worker_claim_token = ?""",
                (
                    error_code, error_message, now_ms, run_id, claim_token,
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_ms = int(time.time() * 1000)
            cursor = connection.execute(
                """UPDATE topic_run
                   SET status = ?, stage = COALESCE(?, stage), updated_at_ms = ?,
                       worker_claim_token = CASE WHEN ? = 'running' THEN worker_claim_token ELSE NULL END,
                       worker_claimed_at_ms = CASE WHEN ? = 'running' THEN worker_claimed_at_ms ELSE NULL END,
                       worker_lease_expires_at_ms = CASE WHEN ? = 'running' THEN worker_lease_expires_at_ms ELSE NULL END
                   WHERE run_id = ? AND (
                       (worker_claim_token IS NULL AND ? IS NULL)
                       OR (worker_claim_token = ? AND worker_lease_expires_at_ms > ?)
                   ) AND publication_json IS NULL""",
                (
                    status, stage, now_ms, status, status, status, run_id,
                    worker_claim_token, worker_claim_token, now_ms,
                ),
            )
        if cursor.rowcount != 1:
            self._raise_missing_or_lost(run_id)

    def _get_raw(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM topic_run WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return a run after finishing any durable terminal publication."""
        self.recover_terminal_publication(run_id)
        return self._get_raw(run_id)

    def publish_terminal_artifacts(
        self,
        run_id: str,
        *,
        expected_manifest_json: str,
        manifest: Mapping[str, Any],
        stage: str,
        status: str | None,
        files: Mapping[str, bytes],
        delete_names: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Durably publish a review/verify/export mutation with manifest CAS."""
        if status is not None and status not in RUN_STATES:
            raise ValueError(f"unsupported topic run status: {status}")
        if not isinstance(stage, str) or not stage:
            raise ValueError("terminal publication stage is required")
        self.recover_terminal_publication(run_id)
        run = self._get_raw(run_id)
        if run is None:
            raise KeyError(run_id)
        artifact_dir = Path(str(run["artifact_dir"])).resolve()
        normalized_files: dict[str, bytes] = {}
        for name, raw in files.items():
            if not self._terminal_name_safe(name):
                raise ValueError("invalid terminal artifact name")
            if not isinstance(raw, bytes):
                raise TypeError("terminal artifact content must be bytes")
            normalized_files[name] = raw
        normalized_deletes = tuple(dict.fromkeys(delete_names))
        if any(not self._terminal_name_safe(name) for name in normalized_deletes):
            raise ValueError("invalid terminal artifact name")
        if set(normalized_files) & set(normalized_deletes):
            raise ValueError("terminal artifact cannot be published and deleted")

        publication_id = secrets.token_hex(16)
        publication_root = artifact_dir / ".publications"
        publication_root.mkdir(parents=True, exist_ok=True)
        self._fsync_directory(artifact_dir)
        publication_dir = publication_root / publication_id
        publication_dir.mkdir(exist_ok=False)
        self._fsync_directory(publication_root)
        file_records: dict[str, dict[str, str]] = {}
        try:
            for name, raw in normalized_files.items():
                staged = publication_dir / name
                with staged.open("wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                file_records[name] = {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            self._fsync_directory(publication_dir)

            proposed = json.loads(json.dumps(dict(manifest)))
            artifacts = proposed.setdefault("artifacts", {})
            if not isinstance(artifacts, dict):
                raise ValueError("terminal manifest artifacts must be an object")
            for name, record in file_records.items():
                artifacts[name] = record["sha256"]
            for name in normalized_deletes:
                artifacts.pop(name, None)
            proposed["stage"] = stage
            publication = {
                "publication_id": publication_id,
                "manifest": proposed,
                "stage": stage,
                "status": status,
                "files": file_records,
                "delete_names": list(normalized_deletes),
            }
            publication_json = json.dumps(
                publication, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """UPDATE topic_run SET publication_json = ?
                       WHERE run_id = ? AND manifest_json = ?
                         AND publication_json IS NULL
                         AND worker_claim_token IS NULL
                         AND status IN ('review_ready', 'verified')""",
                    (publication_json, run_id, expected_manifest_json),
                )
            if cursor.rowcount != 1:
                raise RunPublicationConflictError("run_publication_conflict")
            self.recover_terminal_publication(run_id)
            return proposed
        except Exception:
            current = self._get_raw(run_id)
            keep_staging = False
            if current is not None and current.get("publication_json"):
                try:
                    keep_staging = json.loads(
                        str(current["publication_json"])
                    ).get("publication_id") == publication_id
                except (AttributeError, json.JSONDecodeError):
                    keep_staging = False
            if not keep_staging:
                shutil.rmtree(publication_dir, ignore_errors=True)
            raise

    def recover_terminal_publication(self, run_id: str) -> bool:
        """Idempotently finish a terminal publication recorded before a crash."""
        publication_dir: Path | None = None
        manifest: dict[str, Any] | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT artifact_dir, publication_json FROM topic_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None or row["publication_json"] is None:
                return False
            raw_publication = str(row["publication_json"])
            try:
                publication = json.loads(raw_publication)
                publication_id = str(publication["publication_id"])
                manifest = publication["manifest"]
                files = publication["files"]
                delete_names = publication["delete_names"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RunPublicationConflictError("invalid_run_publication") from exc
            if (
                not isinstance(manifest, dict)
                or not isinstance(files, dict)
                or not isinstance(delete_names, list)
                or not publication_id
            ):
                raise RunPublicationConflictError("invalid_run_publication")
            artifact_dir = Path(str(row["artifact_dir"])).resolve()
            publication_dir = (
                artifact_dir / ".publications" / publication_id
            ).resolve()
            try:
                publication_dir.relative_to(artifact_dir / ".publications")
            except ValueError as exc:
                raise RunPublicationConflictError("invalid_run_publication") from exc
            for name, record in files.items():
                if (
                    not self._terminal_name_safe(name)
                    or not isinstance(record, dict)
                    or not isinstance(record.get("sha256"), str)
                ):
                    raise RunPublicationConflictError("invalid_run_publication")
                staged = publication_dir / name
                if not staged.is_file() or self._sha256(staged) != record["sha256"]:
                    raise RunPublicationConflictError("invalid_run_publication")
                target = artifact_dir / name
                temporary = target.with_name(
                    f".{target.name}.{publication_id}.tmp"
                )
                try:
                    with staged.open("rb") as source, temporary.open("wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            destination.write(chunk)
                        destination.flush()
                        os.fsync(destination.fileno())
                    os.replace(temporary, target)
                    self._fsync_directory(target.parent)
                finally:
                    temporary.unlink(missing_ok=True)
            for name in delete_names:
                if not self._terminal_name_safe(name):
                    raise RunPublicationConflictError("invalid_run_publication")
                (artifact_dir / name).unlink(missing_ok=True)
            self._fsync_directory(artifact_dir)
            self._finalize_terminal_publication(
                connection, run_id, raw_publication, publication,
            )

        assert publication_dir is not None and manifest is not None
        try:
            self._write_manifest_mirror(Path(str(row["artifact_dir"])), manifest)
        except OSError:
            # The database is authoritative; verification repairs this cache.
            pass
        shutil.rmtree(publication_dir, ignore_errors=True)
        return True

    def _finalize_terminal_publication(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        raw_publication: str,
        publication: Mapping[str, Any],
    ) -> None:
        manifest_json = json.dumps(
            publication["manifest"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        now_ms = int(time.time() * 1000)
        cursor = connection.execute(
            """UPDATE topic_run
               SET stage = ?, status = COALESCE(?, status),
                   error_code = NULL, error_message = NULL,
                   manifest_json = ?, publication_json = NULL,
                   updated_at_ms = ?
               WHERE run_id = ? AND publication_json = ?""",
            (
                publication["stage"], publication.get("status"),
                manifest_json, now_ms, run_id, raw_publication,
            ),
        )
        if cursor.rowcount != 1:
            raise RunPublicationConflictError("run_publication_conflict")

    @staticmethod
    def _terminal_name_safe(name: Any) -> bool:
        return (
            isinstance(name, str)
            and bool(name)
            and name != "manifest.json"
            and Path(name).name == name
            and "/" not in name
            and "\\" not in name
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_manifest_mirror(artifact_dir: Path, manifest: Mapping[str, Any]) -> None:
        target = artifact_dir / "manifest.json"
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    dict(manifest), ensure_ascii=False, sort_keys=True, indent=2,
                ) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            TopicRunStore._fsync_directory(artifact_dir)
        finally:
            temporary.unlink(missing_ok=True)

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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_ms = int(time.time() * 1000)
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
                   ) AND publication_json IS NULL""",
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            checked_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
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
    ) -> dict[Path, str]:
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
        prepared: list[tuple[Path, Path, str]] = []
        try:
            for source_path, target_path in normalized:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = target_path.with_name(
                    f".{target_path.name}.{token_hash}.{secrets.token_hex(8)}.tmp"
                )
                digest = hashlib.sha256()
                try:
                    with source_path.open("rb") as source_file, temporary.open("wb") as target_file:
                        while chunk := source_file.read(1024 * 1024):
                            target_file.write(chunk)
                            digest.update(chunk)
                        target_file.flush()
                        os.fsync(target_file.fileno())
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise
                prepared.append((temporary, target_path, digest.hexdigest()))

            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                # Sample after lock acquisition: waiting for another writer
                # must not let an already expired lease pass this fence.
                promotion_at_ms = int(time.time() * 1000)
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
                for temporary, target_path, _digest in prepared:
                    os.replace(temporary, target_path)
            return {
                target_path: digest
                for _temporary, target_path, digest in prepared
            }
        finally:
            for temporary, _target_path, _digest in prepared:
                temporary.unlink(missing_ok=True)

    def _raise_missing_or_lost(self, run_id: str) -> None:
        current = self._get_raw(run_id)
        if current is None:
            raise KeyError(run_id)
        if current.get("publication_json"):
            raise RunPublicationConflictError("run_publication_conflict")
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
    ) -> dict[Path, str]:
        self.ensure_worker_claim(run_id)
        return self._store.publish_worker_files(
            run_id, self._claim_token, files,
        )

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
