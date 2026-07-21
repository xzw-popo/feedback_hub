"""Immutable value objects used by vector index metadata operations."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingFeedback:
    feedback_id: str
    conversation_id: str
    ts_ms: int
    platform: str
    channel: str
    appversion: str
    text: str
    content_hash: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PendingFeedback":
        text = str(row["text"])
        return cls(
            feedback_id=str(row["feedback_id"]),
            conversation_id=str(row["conversation_id"]),
            ts_ms=int(row["ts_ms"]),
            platform=str(row["platform"] or ""),
            channel=str(row["channel"] or ""),
            appversion=str(row["appversion"] or ""),
            text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class ShardMetadata:
    shard_id: str
    model_version: str
    path: str
    dimension: int
    row_count: int
    checksum: str


@dataclass(frozen=True)
class EmbeddingRecord:
    feedback_id: str
    model_version: str
    content_hash: str
    shard_id: str
    row_offset: int
    embedded_at_ms: int


@dataclass(frozen=True)
class VectorIndexStatus:
    active_shards: int
    retired_shards: int
