"""Read-only source snapshots and normalized topic-mining source items."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    coverage_watermark_ms: int | None


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


class SourceReadinessError(ValueError):
    """Raised when source identities are not ready for the requested unit."""


_FIXED_SOURCE_PRODUCT = "微信输入法"


def source_freshness(
    source_path: Path,
    *,
    observed_at_ms: int | None = None,
    sync_interval_seconds: int = 1_200,
) -> dict[str, int | bool | None]:
    """Describe the newest complete interval shared by all recorded channels."""
    observed = int(time.time() * 1000) if observed_at_ms is None else int(observed_at_ms)
    unavailable: dict[str, int | bool | None] = {
        "ready": False,
        "available_from_ms": None,
        "available_through_ms": None,
        "source_generation_ms": None,
        "observed_at_ms": observed,
        "freshness_lag_seconds": None,
        "sync_interval_seconds": int(sync_interval_seconds),
    }
    try:
        with _readonly_connection(Path(source_path)) as connection:
            rows = connection.execute(
                """SELECT channel, start_ts_ms, end_ts_ms, completed_at_ms
                   FROM feedback_source_coverage
                   WHERE TRIM(channel) <> '' AND end_ts_ms > start_ts_ms
                   ORDER BY channel, start_ts_ms, end_ts_ms"""
            ).fetchall()
    except sqlite3.Error:
        return unavailable
    if not rows:
        return unavailable

    by_channel: dict[str, list[tuple[int, int]]] = {}
    generation_ms = max(int(row[3]) for row in rows)
    for channel, start_ms, end_ms, _completed_at_ms in rows:
        intervals = by_channel.setdefault(str(channel), [])
        start, end = int(start_ms), int(end_ms)
        if intervals and start <= intervals[-1][1]:
            previous_start, previous_end = intervals[-1]
            intervals[-1] = (previous_start, max(previous_end, end))
        else:
            intervals.append((start, end))

    latest_segments = [intervals[-1] for intervals in by_channel.values() if intervals]
    common_start = max(start for start, _end in latest_segments)
    common_end = min(end for _start, end in latest_segments)
    if common_start >= common_end:
        unavailable["source_generation_ms"] = generation_ms
        return unavailable
    return {
        "ready": True,
        "available_from_ms": common_start,
        "available_through_ms": common_end,
        "source_generation_ms": generation_ms,
        "observed_at_ms": observed,
        "freshness_lag_seconds": max(0, (observed - common_end) // 1000),
        "sync_interval_seconds": int(sync_interval_seconds),
    }


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
        try:
            coverage_row = snapshot.execute(
                "SELECT MAX(completed_at_ms) FROM feedback_source_coverage",
            ).fetchone()
        except sqlite3.Error:
            coverage_row = None
    return SourceSnapshot(
        path=snapshot_path,
        sha256=_sha256(snapshot_path),
        byte_size=snapshot_path.stat().st_size,
        min_ts_ms=row[0],
        max_ts_ms=row[1],
        row_count=row[2],
        coverage_watermark_ms=(
            coverage_row[0]
            if coverage_row is not None
            else None
        ),
    )


def _scope_ms(spec: TopicSpec) -> tuple[int, int]:
    start = int(spec.scope.start_time.astimezone(timezone.utc).timestamp() * 1000)
    end = int(spec.scope.end_time.astimezone(timezone.utc).timestamp() * 1000)
    return start, end


def _source_coverage(
    connection: sqlite3.Connection,
    spec: TopicSpec,
    start_ms: int,
    end_ms: int,
) -> None:
    try:
        if spec.scope.channels:
            channels = list(spec.scope.channels)
        else:
            channels = [
                str(row[0])
                for row in connection.execute(
                    """SELECT DISTINCT channel FROM (
                        SELECT channel FROM feedback
                        UNION ALL
                        SELECT channel FROM feedback_source_coverage
                    ) WHERE TRIM(channel) <> '' ORDER BY channel"""
                )
            ]
    except sqlite3.Error as exc:
        raise DataCoverageError(start_ms, end_ms, None, None) from exc
    if not channels:
        raise DataCoverageError(start_ms, end_ms, None, None)
    for channel in channels:
        try:
            rows = connection.execute(
                """SELECT start_ts_ms, end_ts_ms
                   FROM feedback_source_coverage
                   WHERE channel = ? AND end_ts_ms > ? AND start_ts_ms < ?
                   ORDER BY start_ts_ms, end_ts_ms""",
                (channel, start_ms, end_ms),
            ).fetchall()
        except sqlite3.Error as exc:
            raise DataCoverageError(start_ms, end_ms, None, None) from exc
        cursor = start_ms
        for covered_start, covered_end in rows:
            if covered_end <= cursor:
                continue
            if covered_start > cursor:
                break
            cursor = max(cursor, int(covered_end))
            if cursor >= end_ms:
                break
        if cursor < end_ms:
            source_min_ms = min((int(row[0]) for row in rows), default=None)
            source_max_ms = max((int(row[1]) for row in rows), default=None)
            raise DataCoverageError(
                start_ms, end_ms, source_min_ms, source_max_ms,
            )


def _filtered_rows(connection: sqlite3.Connection, spec: TopicSpec) -> list[sqlite3.Row]:
    start_ms, end_ms = _scope_ms(spec)
    _source_coverage(connection, spec, start_ms, end_ms)
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


def validate_source_coverage(source_path: Path, spec: TopicSpec) -> None:
    """Require explicit continuous pull coverage without reading result rows."""
    _validate_source_filters(spec)
    start_ms, end_ms = _scope_ms(spec)
    with _readonly_connection(Path(source_path)) as connection:
        _source_coverage(connection, spec, start_ms, end_ms)


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
        "service_vid": row["service_vid"],
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

    if any(_conversation_id_unready(row["conversation_id"]) for row in rows):
        raise SourceReadinessError("conversation_source_not_ready")

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
            "service_vid": first["service_vid"],
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
        identity = _context_identity(item)
        contexts[item["item_id"]] = [
            candidate for candidate in ordered
            if _context_identity(candidate) == identity
            and abs(candidate["ts_ms"] - item["ts_ms"]) <= window_ms
        ]
    return contexts


def _conversation_id_unready(value: Any) -> bool:
    return not isinstance(value, str) or value.strip().lower() in {
        "", "pending", "unknown", "none", "null",
    }


def _context_identity(item: Mapping[str, Any]) -> tuple[str, ...]:
    conversation_id = item.get("conversation_id")
    if not _conversation_id_unready(conversation_id):
        return ("conversation", str(conversation_id).strip())
    user_vid = item.get("user_vid")
    if isinstance(user_vid, str) and user_vid.strip():
        return (
            "user", str(item.get("channel") or ""),
            str(item.get("service_vid") or ""), user_vid.strip(),
        )
    return ("item", str(item.get("item_id") or ""))
