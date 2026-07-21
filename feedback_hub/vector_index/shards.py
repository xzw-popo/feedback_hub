"""Immutable NumPy shard files and crash-safe manifest publication.

The SQLite database owns feedback-to-row mappings.  This module owns the
immutable vector files and the manifest that makes one complete generation
visible to readers.  A small durable journal bridges the two stores whenever
compaction changes mappings.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import numpy as np

from .config import VectorIndexConfig
from .models import ShardMetadata
from .repository import VectorRepository


MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ShardManifest:
    """The complete, ordered, reader-visible vector generation."""

    schema_version: int
    generation: int
    model_version: str
    dimension: int
    watermark_ts_ms: int
    shards: tuple[ShardMetadata, ...]
    previous_watermark_ts_ms: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_vectors(
    vectors: np.ndarray, *, expected_rows: int, dimension: int
) -> None:
    """Reject data that cannot safely be published as a cosine-search shard."""
    if not isinstance(vectors, np.ndarray) or vectors.ndim != 2:
        raise ValueError("vectors must be a two-dimensional NumPy array")
    if vectors.dtype != np.float32:
        raise ValueError("vectors must have dtype float32")
    if vectors.shape[0] != expected_rows:
        raise ValueError("vector count does not match feedback IDs")
    if expected_rows <= 0:
        raise ValueError("empty shards are not allowed")
    if vectors.shape[1] != dimension:
        raise ValueError("vector dimension does not match index dimension")
    if not np.isfinite(vectors).all():
        raise ValueError("vectors contain non-finite values")
    norms = np.linalg.norm(vectors.astype(np.float64, copy=False), axis=1)
    if not np.allclose(norms, 1.0, rtol=1e-4, atol=1e-4):
        raise ValueError("vectors must be normalized")


class ShardStore:
    """Store immutable shard files below one relocatable index root."""

    def __init__(self, root: Path | str, *, dimension: int, model_version: str) -> None:
        self.root = Path(root).resolve()
        self.dimension = dimension
        self.model_version = model_version
        self.shards_dir = self.root / "shards"
        self.tmp_dir = self.root / "tmp"
        self.manifest_path = self.root / "manifest.json"
        self.publication_journal_path = self.root / "publication-journal.json"
        self.publication_lock_path = self.root / ".publication.lock"
        self._highest_watermark = 0
        self.root.mkdir(parents=True, exist_ok=True)
        self.shards_dir.mkdir(exist_ok=True)
        self.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def from_config(cls, config: VectorIndexConfig) -> "ShardStore":
        store = cls(config.data_dir, dimension=config.dimension, model_version=config.model_version)
        repository = VectorRepository(config)
        try:
            repository.init_schema()
            with store._publication_lock():
                store.recover_publication(repository)
        finally:
            repository.close()
        return store

    @classmethod
    def open(cls, config: VectorIndexConfig) -> "ShardStore":
        """Open a store only after resolving an interrupted DB/file publication."""
        return cls.from_config(config)

    def shard_path(self, shard: ShardMetadata) -> Path:
        path = Path(shard.path)
        if path.is_absolute():
            candidate = path.resolve()
        else:
            candidate = (self.root / path).resolve()
        try:
            candidate.relative_to(self.shards_dir.resolve())
        except ValueError as exc:
            raise ValueError("shard path escapes the shard directory") from exc
        return candidate

    def write_shard(
        self, vectors: np.ndarray, feedback_ids: Sequence[str]
    ) -> ShardMetadata:
        validate_vectors(vectors, expected_rows=len(feedback_ids), dimension=self.dimension)
        if len(set(feedback_ids)) != len(feedback_ids) or any(not item for item in feedback_ids):
            raise ValueError("feedback IDs must be unique non-empty strings")
        temporary = self.tmp_dir / f".{uuid.uuid4().hex}.npy"
        try:
            with temporary.open("wb") as handle:
                np.save(handle, vectors.astype(np.float32, copy=False), allow_pickle=False)
                handle.flush()
                os.fsync(handle.fileno())
            checksum = sha256_file(temporary)
            final = self.shards_dir / f"shard-{checksum[:16]}.npy"
            if final.exists():
                if sha256_file(final) != checksum:
                    raise ValueError("existing shard filename has a different checksum")
                temporary.unlink()
            else:
                os.replace(temporary, final)
                self._fsync_directory(self.shards_dir)
        finally:
            if temporary.exists():
                temporary.unlink()
        return ShardMetadata(
            shard_id=checksum[:24], model_version=self.model_version, path=str(final),
            dimension=self.dimension, row_count=len(feedback_ids), checksum=checksum,
        )

    def manifest_for(
        self, shards: Sequence[ShardMetadata], *, watermark_ts_ms: int
    ) -> ShardManifest:
        previous = self._read_manifest_if_present()
        previous_watermark = previous.watermark_ts_ms if previous is not None else 0
        if watermark_ts_ms < previous_watermark:
            raise ValueError("manifest watermark must not decrease")
        return ShardManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            generation=(previous.generation + 1) if previous is not None else 1,
            model_version=self.model_version,
            dimension=self.dimension,
            watermark_ts_ms=int(watermark_ts_ms),
            previous_watermark_ts_ms=previous_watermark,
            shards=tuple(shards),
        )

    def publish_manifest(
        self, shards: Sequence[ShardMetadata], *, watermark_ts_ms: int
    ) -> ShardManifest:
        with self._publication_lock():
            if len({shard.shard_id for shard in shards}) != len(shards):
                raise ValueError("manifest contains duplicate shard IDs")
            for shard in shards:
                self.verify_shard(shard)
            manifest = self.manifest_for(shards, watermark_ts_ms=watermark_ts_ms)
            self._replace_manifest(manifest, expected_previous=self._read_manifest_if_present())
            return manifest

    def publish_from_repository(
        self, repository: VectorRepository, *, watermark_ts_ms: int
    ) -> ShardManifest:
        raise RuntimeError(
            "database rows must be published through publish_with_database before commit"
        )

    def publish_with_database(
        self,
        repository: VectorRepository,
        *,
        old_manifest: ShardManifest | None,
        new_manifest: ShardManifest,
        new_shards: Sequence[ShardMetadata],
        row_remap: Sequence[dict[str, Any]],
        database_mutation: Callable[[], None],
    ) -> ShardManifest:
        """Commit one journaled SQLite/manifest generation under one writer lock.

        Caller-owned SQLite transactions are rejected before writing a journal:
        otherwise neither this method nor SQLite can know whether the caller
        will commit before a manifest becomes visible.
        """
        if repository.connection.in_transaction:
            raise RuntimeError("coordinated publication requires no caller-owned transaction")
        with self._publication_lock():
            current = self._read_manifest_if_present()
            if not self._same_manifest(current, old_manifest):
                raise RuntimeError("stale manifest generation")
            self._validate_manifest_for_publication(new_manifest, previous=old_manifest)
            if old_manifest is None and (
                new_manifest.generation != 1 or new_manifest.previous_watermark_ts_ms != 0
            ):
                raise ValueError("manifest generation is not the first generation")
            self._validate_remap(new_manifest, row_remap)
            self.write_publication_journal(old_manifest, new_manifest, new_shards, row_remap)
            try:
                repository.connection.execute("BEGIN IMMEDIATE")
                database_mutation()
                repository.connection.commit()
            except Exception:
                repository.connection.rollback()
                raise
            if not self._database_matches_journal(repository, new_manifest, row_remap):
                raise ValueError("committed database state does not match publication journal")
            self._replace_manifest(new_manifest, expected_previous=old_manifest)
            self._remove_journal()
            return new_manifest

    def load_manifest(self) -> ShardManifest:
        manifest = self._read_manifest_if_present()
        if manifest is None:
            raise FileNotFoundError(self.manifest_path)
        if manifest.watermark_ts_ms < self._highest_watermark:
            raise ValueError("manifest watermark decreased")
        self._highest_watermark = manifest.watermark_ts_ms
        return manifest

    def verify_shard(self, shard: ShardMetadata) -> None:
        if shard.model_version != self.model_version:
            raise ValueError("shard model version does not match manifest")
        if shard.dimension != self.dimension:
            raise ValueError("shard dimension does not match manifest")
        path = self.shard_path(shard)
        if shard.shard_id != shard.checksum[:24] or path.name != f"shard-{shard.checksum[:16]}.npy":
            raise ValueError("shard identity does not match checksum-derived path")
        if not path.is_file():
            raise ValueError("manifest shard file is missing")
        if sha256_file(path) != shard.checksum:
            raise ValueError("manifest shard checksum mismatch")
        try:
            matrix = np.load(path, mmap_mode="r", allow_pickle=False)
        except Exception as exc:
            raise ValueError("manifest shard cannot be loaded") from exc
        validate_vectors(matrix, expected_rows=shard.row_count, dimension=self.dimension)

    def write_publication_journal(
        self,
        old_manifest: ShardManifest | None,
        new_manifest: ShardManifest,
        new_shards: Sequence[ShardMetadata],
        row_remap: Sequence[dict[str, Any]],
    ) -> None:
        """Persist publication intent before the corresponding SQLite mutation."""
        payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "old_manifest": self._manifest_payload(old_manifest) if old_manifest else None,
            "new_manifest": self._manifest_payload(new_manifest),
            "new_shards": [self._shard_payload(item) for item in new_shards],
            "row_remap": [dict(item) for item in row_remap],
        }
        self._atomic_json_replace(self.publication_journal_path, payload)

    def recover_publication(self, repository: VectorRepository) -> bool:
        """Finish a committed publication or restore the former generation.

        This must run before serving readers.  SQLite is the deciding record of
        whether the intended mapping transaction committed.
        """
        if not self.publication_journal_path.exists():
            return False
        payload = self._read_json(self.publication_journal_path)
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unknown publication journal schema version")
        old_payload = payload.get("old_manifest")
        new_payload = payload.get("new_manifest")
        if old_payload is not None and not isinstance(old_payload, dict):
            raise ValueError("invalid old publication manifest")
        if not isinstance(new_payload, dict):
            raise ValueError("invalid new publication manifest")
        # Decide using SQLite before touching new files.  A previous rollback may
        # already have deleted replacement files but crashed before unlinking the
        # journal; that journal must remain recoverable and idempotent.
        new_manifest = self._manifest_from_payload(new_payload, verify_files=False)
        new_shards_raw = payload.get("new_shards")
        remap = payload.get("row_remap")
        if not isinstance(new_shards_raw, list) or not isinstance(remap, list):
            raise ValueError("invalid publication journal")
        new_shards = tuple(self._shard_from_payload(item) for item in new_shards_raw)
        old_manifest = (
            self._manifest_from_payload(old_payload, verify_files=False)
            if old_payload is not None else None
        )
        live = self._read_manifest_if_present()
        if self._is_newer_manifest(live, new_manifest):
            # A later generation won the CAS race.  Never restore either side
            # of a stale journal over it.
            self._remove_journal()
            return True
        if self._database_matches_journal(repository, new_manifest, remap):
            self._validate_manifest_for_publication(new_manifest)
            if not self._same_manifest(live, new_manifest):
                self._replace_manifest(new_manifest, expected_previous=old_manifest)
        else:
            if self._same_manifest(live, new_manifest):
                if old_manifest is None:
                    self.manifest_path.unlink(missing_ok=True)
                    self._fsync_directory(self.root)
                else:
                    self._validate_manifest_for_publication(old_manifest)
                    self._replace_manifest(old_manifest, expected_previous=new_manifest)
            elif not self._same_manifest(live, old_manifest):
                raise ValueError("publication journal does not match live generation")
            referenced = self._database_paths(repository)
            if old_manifest is not None:
                referenced.update(self.shard_path(shard) for shard in old_manifest.shards)
            if live is not None:
                referenced.update(self.shard_path(shard) for shard in live.shards)
            for shard in new_shards:
                path = self.shard_path(shard)
                if path not in referenced and path.exists():
                    path.unlink()
            self._fsync_directory(self.shards_dir)
        self._remove_journal()
        return True

    def find_orphans(self, repository: VectorRepository) -> list[Path]:
        """Return, but never remove, immutable files with no durable reference."""
        referenced = self._database_paths(repository)
        live = self._read_manifest_if_present()
        if live is not None:
            referenced.update(self.shard_path(shard) for shard in live.shards)
        if self.publication_journal_path.exists():
            journal = self._read_json(self.publication_journal_path)
            if journal.get("schema_version") != MANIFEST_SCHEMA_VERSION:
                raise ValueError("unknown publication journal schema version")
            new_payload = journal.get("new_manifest")
            row_remap = journal.get("row_remap")
            if not isinstance(new_payload, dict) or not isinstance(row_remap, list):
                raise ValueError("invalid publication journal")
            self._validate_remap(
                self._manifest_from_payload(new_payload, verify_files=False), row_remap
            )
            for key in ("old_manifest", "new_manifest"):
                item = journal.get(key)
                if isinstance(item, dict):
                    manifest = self._manifest_from_payload(item, verify_files=False)
                    referenced.update(self.shard_path(shard) for shard in manifest.shards)
                elif item is not None:
                    raise ValueError("invalid publication journal manifest")
            raw_new_shards = journal.get("new_shards")
            if not isinstance(raw_new_shards, list):
                raise ValueError("invalid publication journal")
            referenced.update(self.shard_path(self._shard_from_payload(raw)) for raw in raw_new_shards)
        return sorted(path.resolve() for path in self.shards_dir.glob("*.npy") if path.resolve() not in referenced)

    def _read_manifest_if_present(self) -> ShardManifest | None:
        if not self.manifest_path.exists():
            return None
        return self._manifest_from_payload(self._read_json(self.manifest_path), verify_files=True)

    def _manifest_from_payload(self, payload: Any, *, verify_files: bool) -> ShardManifest:
        if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unknown manifest schema version")
        if payload.get("model_version") != self.model_version:
            raise ValueError("manifest model version does not match store")
        if payload.get("dimension") != self.dimension:
            raise ValueError("manifest dimension does not match store")
        generation = payload.get("generation")
        watermark = payload.get("watermark_ts_ms")
        previous = payload.get("previous_watermark_ts_ms", 0)
        raw_shards = payload.get("shards")
        if (not isinstance(generation, int) or generation < 1 or not isinstance(watermark, int)
                or watermark < 0 or not isinstance(previous, int) or previous < 0
                or watermark < previous or not isinstance(raw_shards, list)):
            raise ValueError("invalid manifest generation or watermark")
        shards = tuple(self._shard_from_payload(raw) for raw in raw_shards)
        if len({item.shard_id for item in shards}) != len(shards):
            raise ValueError("manifest contains duplicate shard IDs")
        manifest = ShardManifest(
            schema_version=MANIFEST_SCHEMA_VERSION, generation=generation,
            model_version=self.model_version, dimension=self.dimension,
            watermark_ts_ms=watermark, previous_watermark_ts_ms=previous, shards=shards,
        )
        if verify_files:
            for shard in shards:
                self.verify_shard(shard)
        return manifest

    def _shard_from_payload(self, payload: Any) -> ShardMetadata:
        if not isinstance(payload, dict):
            raise ValueError("invalid manifest shard")
        try:
            relative_path = payload["path"]
            if not isinstance(relative_path, str) or Path(relative_path).is_absolute():
                raise ValueError("manifest shard path must be relative")
            path = (self.root / relative_path).resolve()
            path.relative_to(self.shards_dir.resolve())
            metadata = ShardMetadata(
                shard_id=str(payload["shard_id"]), model_version=str(payload["model_version"]),
                path=str(path), dimension=int(payload["dimension"]),
                row_count=int(payload["row_count"]), checksum=str(payload["checksum"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid manifest shard") from exc
        if metadata.path != str(self.shards_dir / f"shard-{metadata.checksum[:16]}.npy"):
            raise ValueError("manifest shard path does not match its checksum")
        if metadata.shard_id != metadata.checksum[:24] or metadata.row_count <= 0:
            raise ValueError("invalid manifest shard metadata")
        return metadata

    def _manifest_payload(self, manifest: ShardManifest) -> dict[str, Any]:
        return {
            "schema_version": manifest.schema_version, "generation": manifest.generation,
            "model_version": manifest.model_version, "dimension": manifest.dimension,
            "watermark_ts_ms": manifest.watermark_ts_ms,
            "previous_watermark_ts_ms": manifest.previous_watermark_ts_ms,
            "shards": [self._shard_payload(item) for item in manifest.shards],
        }

    def _shard_payload(self, shard: ShardMetadata) -> dict[str, Any]:
        path = self.shard_path(shard)
        return {
            "shard_id": shard.shard_id, "model_version": shard.model_version,
            "path": path.relative_to(self.root).as_posix(), "dimension": shard.dimension,
            "row_count": shard.row_count, "checksum": shard.checksum,
        }

    def _replace_manifest(
        self, manifest: ShardManifest, *, expected_previous: ShardManifest | None = None
    ) -> None:
        current = self._read_manifest_if_present()
        if expected_previous is not None and not self._same_manifest(current, expected_previous):
            raise RuntimeError("manifest generation changed before publication")
        if current is not None and self._is_newer_manifest(current, manifest):
            raise RuntimeError("manifest generation would regress")
        self._atomic_json_replace(self.manifest_path, self._manifest_payload(manifest))
        self._highest_watermark = max(self._highest_watermark, manifest.watermark_ts_ms)

    def _atomic_json_replace(self, destination: Path, payload: dict[str, Any]) -> None:
        temporary = destination.with_name(f".{destination.name}.tmp")
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        self._fsync_directory(destination.parent)

    def _remove_journal(self) -> None:
        if self.publication_journal_path.exists():
            self.publication_journal_path.unlink()
            self._fsync_directory(self.root)

    def _database_matches_journal(
        self, repository: VectorRepository, manifest: ShardManifest, remap: Sequence[Any]
    ) -> bool:
        connection = repository.connection
        try:
            self._validate_remap(manifest, remap)
        except ValueError:
            return False
        manifest_ids = {shard.shard_id for shard in manifest.shards}
        active_rows = connection.execute(
            """SELECT shard_id, model_version, dimension, row_count, checksum
               FROM embedding_shard WHERE state = 'active' AND model_version = ?""",
            (self.model_version,),
        ).fetchall()
        if {str(row[0]) for row in active_rows} != manifest_ids:
            return False
        by_id = {shard.shard_id: shard for shard in manifest.shards}
        for row in active_rows:
            shard = by_id[str(row[0])]
            if tuple(row[1:]) != (
                shard.model_version, shard.dimension, shard.row_count, shard.checksum
            ):
                return False
        expected = {
            (str(item["feedback_id"]), str(item["model_version"]), str(item["content_hash"])):
            (str(item["shard_id"]), int(item["row_offset"]))
            for item in remap
        }
        rows = connection.execute(
            """SELECT feedback_id, model_version, content_hash, shard_id, row_offset
               FROM embedding_record WHERE shard_id IN (%s)"""
            % ",".join("?" for _ in manifest.shards),
            tuple(manifest_ids),
        ).fetchall() if manifest.shards else []
        actual = {
            (str(row[0]), str(row[1]), str(row[2])): (str(row[3]), int(row[4]))
            for row in rows
        }
        if len(actual) != len(rows) or actual != expected:
            return False
        for shard in manifest.shards:
            offsets = [offset for assigned, offset in actual.values() if assigned == shard.shard_id]
            if sorted(offsets) != list(range(shard.row_count)):
                return False
        return True

    def _validate_remap(self, manifest: ShardManifest, remap: Sequence[Any]) -> None:
        expected_rows = sum(shard.row_count for shard in manifest.shards)
        if len(remap) != expected_rows:
            raise ValueError("publication remap must contain every vector row")
        metadata = {shard.shard_id: shard for shard in manifest.shards}
        identities: set[tuple[str, str, str]] = set()
        feedback_ids: set[str] = set()
        positions: set[tuple[str, int]] = set()
        for item in remap:
            if not isinstance(item, dict):
                raise ValueError("invalid publication remap")
            try:
                identity = (str(item["feedback_id"]), str(item["model_version"]), str(item["content_hash"]))
                shard_id, row_offset = str(item["shard_id"]), int(item["row_offset"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid publication remap") from exc
            shard = metadata.get(shard_id)
            if (not all(identity) or shard is None or identity[1] != self.model_version
                    or row_offset < 0 or row_offset >= shard.row_count):
                raise ValueError("invalid publication remap")
            if identity in identities or (shard_id, row_offset) in positions:
                raise ValueError("publication remap is not one-to-one")
            if identity[0] in feedback_ids:
                raise ValueError("publication remap has duplicate feedback_id")
            identities.add(identity)
            feedback_ids.add(identity[0])
            positions.add((shard_id, row_offset))

    def _validate_manifest_for_publication(
        self, manifest: ShardManifest, *, previous: ShardManifest | None = None
    ) -> None:
        if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unknown manifest schema version")
        if manifest.model_version != self.model_version or manifest.dimension != self.dimension:
            raise ValueError("manifest does not match shard store")
        if previous is not None and (
            manifest.generation != previous.generation + 1
            or manifest.previous_watermark_ts_ms != previous.watermark_ts_ms
            or manifest.watermark_ts_ms < previous.watermark_ts_ms
        ):
            raise ValueError("manifest generation or watermark is not the next generation")
        for shard in manifest.shards:
            self.verify_shard(shard)

    @staticmethod
    def _same_manifest(left: ShardManifest | None, right: ShardManifest | None) -> bool:
        return left == right

    @staticmethod
    def _is_newer_manifest(left: ShardManifest | None, right: ShardManifest) -> bool:
        return left is not None and (
            left.generation > right.generation
            or (left.generation == right.generation and left.watermark_ts_ms > right.watermark_ts_ms)
        )

    @contextmanager
    def _publication_lock(self) -> Iterator[None]:
        import fcntl

        with self.publication_lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _database_paths(self, repository: VectorRepository) -> set[Path]:
        paths: set[Path] = set()
        rows = repository.connection.execute(
            "SELECT path FROM embedding_shard WHERE model_version = ?", (self.model_version,)
        ).fetchall()
        for row in rows:
            paths.add(self.shard_path(ShardMetadata(
                shard_id="", model_version=self.model_version, path=str(row[0]),
                dimension=self.dimension, row_count=1, checksum="",
            )))
        return paths

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON publication metadata") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid JSON publication metadata")
        return payload

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def compact_active_shards(repository: VectorRepository, store: ShardStore) -> ShardManifest:
    """Replace all active shards with one generation, preserving exact row mappings."""
    old = store.load_manifest()
    if len(old.shards) <= 1:
        return old
    matrices: list[np.ndarray] = []
    remap: list[dict[str, Any]] = []
    row_offset = 0
    for shard in old.shards:
        matrix = np.load(store.shard_path(shard), mmap_mode="r", allow_pickle=False)
        validate_vectors(matrix, expected_rows=shard.row_count, dimension=store.dimension)
        rows = repository.connection.execute(
            """SELECT feedback_id, model_version, content_hash, row_offset
               FROM embedding_record WHERE shard_id = ? ORDER BY row_offset""",
            (shard.shard_id,),
        ).fetchall()
        if len(rows) != shard.row_count or [int(row[3]) for row in rows] != list(range(shard.row_count)):
            raise ValueError("database mappings do not exactly cover an active shard")
        matrices.append(matrix)
        for row in rows:
            remap.append({
                "feedback_id": str(row[0]), "model_version": str(row[1]),
                "content_hash": str(row[2]), "shard_id": "", "row_offset": row_offset,
            })
            row_offset += 1
    replacement = store.write_shard(np.vstack(matrices), [item["feedback_id"] for item in remap])
    remap = [{**item, "shard_id": replacement.shard_id} for item in remap]
    new = store.manifest_for([replacement], watermark_ts_ms=old.watermark_ts_ms)

    def database_mutation() -> None:
        repository._insert_shard(replacement)
        for item in remap:
            cursor = repository.connection.execute(
                """UPDATE embedding_record SET shard_id = ?, row_offset = ?
                   WHERE feedback_id = ? AND model_version = ? AND content_hash = ?""",
                (item["shard_id"], item["row_offset"], item["feedback_id"],
                 item["model_version"], item["content_hash"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("embedding record changed during compaction")
        cursor = repository.connection.execute(
            "UPDATE embedding_shard SET state = 'retired' WHERE state = 'active' AND shard_id IN (%s)"
            % ",".join("?" for _ in old.shards),
            tuple(item.shard_id for item in old.shards),
        )
        if cursor.rowcount != len(old.shards):
            raise ValueError("active shard changed during compaction")

    return store.publish_with_database(
        repository, old_manifest=old, new_manifest=new, new_shards=[replacement],
        row_remap=remap, database_mutation=database_mutation,
    )
