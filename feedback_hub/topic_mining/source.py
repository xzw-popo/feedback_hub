"""Read-only source snapshots and normalized topic-mining source items."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Iterable

from feedback_hub import db

from .contracts import TopicSpec


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    sha256: str
    byte_size: int
    min_ts_ms: int | None
    max_ts_ms: int | None
    row_count: int


class DataCoverageError(ValueError):
    def __init__(self, start_ms: int, end_ms: int, source_min_ms: int | None, source_max_ms: int | None) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.source_min_ms = source_min_ms
        self.source_max_ms = source_max_ms
        super().__init__(
            f"source does not cover [{start_ms}, {end_ms}): "
            f"available [{source_min_ms}, {source_max_ms}]"
        )


class UnsupportedSourceFilterError(ValueError):
    """Raised when a requested scope cannot be represented by this source."""


_FIXED_SOURCE_PRODUCT = "微信输入法"


def _validate_source_filters(spec: TopicSpec) -> None:
    unsupported_products = set(spec.scope.products) - {_FIXED_SOURCE_PRODUCT}
    if unsupported_products:
        requested = ", ".join(sorted(unsupported_products))
        raise UnsupportedSourceFilterError(
            f"source database is fixed to {_FIXED_SOURCE_PRODUCT}; unsupported products: {requested}"
        )


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_source_snapshot(
    source_path: Path,
    snapshot_path: Path,
    *,
    max_ts_ms: int | None = None,
) -> SourceSnapshot:
    """Copy a read-only source and optionally freeze it at a run watermark."""
    source_path, snapshot_path = Path(source_path), Path(snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with _readonly_connection(source_path) as source, sqlite3.connect(snapshot_path) as destination:
        source.backup(destination)
        if max_ts_ms is not None:
            destination.execute(
                "DELETE FROM feedback WHERE ts_ms > ?", (int(max_ts_ms),),
            )
    with _readonly_connection(snapshot_path) as snapshot:
        row = snapshot.execute("SELECT MIN(ts_ms), MAX(ts_ms), COUNT(*) FROM feedback").fetchone()
    return SourceSnapshot(
        path=snapshot_path,
        sha256=_sha256(snapshot_path),
        byte_size=snapshot_path.stat().st_size,
        min_ts_ms=row[0],
        max_ts_ms=row[1],
        row_count=row[2],
    )


def _scope_ms(spec: TopicSpec) -> tuple[int, int]:
    start = int(spec.scope.start_time.astimezone(timezone.utc).timestamp() * 1000)
    end = int(spec.scope.end_time.astimezone(timezone.utc).timestamp() * 1000)
    return start, end


def _source_coverage(connection: sqlite3.Connection, start_ms: int, end_ms: int) -> None:
    source_min_ms, source_max_ms = connection.execute("SELECT MIN(ts_ms), MAX(ts_ms) FROM feedback").fetchone()
    if source_min_ms is None or start_ms < source_min_ms or end_ms > source_max_ms:
        raise DataCoverageError(start_ms, end_ms, source_min_ms, source_max_ms)


def _filtered_rows(connection: sqlite3.Connection, spec: TopicSpec) -> list[sqlite3.Row]:
    start_ms, end_ms = _scope_ms(spec)
    _source_coverage(connection, start_ms, end_ms)
    clauses = ["ts_ms >= ?", "ts_ms < ?", "TRIM(text) <> ''"]
    params: list[Any] = [start_ms, end_ms]
    for column, values in (
        ("platform", spec.scope.platforms),
        ("channel", spec.scope.channels),
        ("appversion", spec.scope.versions),
    ):
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    connection.row_factory = sqlite3.Row
    return connection.execute(
        "SELECT feedback_id, conversation_id, msg_seq, ts_ms, platform, appversion, channel, "
        "device_name, user_vid, service_vid, external_chat_url, text FROM feedback WHERE "
        + " AND ".join(clauses)
        + " ORDER BY ts_ms, feedback_id",
        params,
    ).fetchall()


def _source_url(row: sqlite3.Row) -> str:
    return row["external_chat_url"] or db.build_external_chat_url(
        row["channel"], row["service_vid"], row["user_vid"]
    ) or ""


def _item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "item_id": str(row["feedback_id"]),
        "feedback_id": str(row["feedback_id"]),
        "conversation_id": str(row["conversation_id"]),
        "ts_ms": int(row["ts_ms"]),
        "platform": row["platform"] or "",
        "appversion": row["appversion"] or "",
        "channel": row["channel"] or "",
        "device_name": row["device_name"] or "",
        "user_vid": row["user_vid"] or "",
        "text": row["text"].strip(),
        "source_url": _source_url(row),
    }


def fetch_scoped_items(source_path: Path, spec: TopicSpec) -> list[dict[str, Any]]:
    """Read source items only through SQLite URI read-only mode."""
    _validate_source_filters(spec)
    with _readonly_connection(Path(source_path)) as connection:
        rows = _filtered_rows(connection, spec)
    if spec.unit == "feedback":
        return [_item(row) for row in rows]

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["conversation_id"]), []).append(row)
    items: list[dict[str, Any]] = []
    for conversation_id, messages in grouped.items():
        messages.sort(key=lambda row: (row["msg_seq"], row["ts_ms"], row["feedback_id"]))
        first = messages[0]
        items.append({
            "item_id": conversation_id,
            "feedback_id": None,
            "conversation_id": conversation_id,
            "ts_ms": min(int(row["ts_ms"]) for row in messages),
            "platform": first["platform"] or "",
            "appversion": first["appversion"] or "",
            "channel": first["channel"] or "",
            "device_name": first["device_name"] or "",
            "user_vid": first["user_vid"] or "",
            "text": "\n".join(row["text"].strip() for row in messages),
            "source_url": _source_url(first),
        })
    return sorted(items, key=lambda item: (item["ts_ms"], item["item_id"]))


def build_item_contexts(items: Iterable[dict[str, Any]], unit: str, window_ms: int) -> dict[str, list[dict[str, Any]]]:
    if unit not in {"feedback", "conversation"}:
        raise ValueError("unit must be feedback or conversation")
    ordered = sorted(items, key=lambda item: (item["ts_ms"], item["item_id"]))
    contexts: dict[str, list[dict[str, Any]]] = {}
    for item in ordered:
        contexts[item["item_id"]] = [
            candidate for candidate in ordered
            if candidate["user_vid"] == item["user_vid"]
            and abs(candidate["ts_ms"] - item["ts_ms"]) <= window_ms
        ]
    return contexts
