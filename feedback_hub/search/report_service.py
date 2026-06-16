"""Search report persistence and generation helpers."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from feedback_hub import db

REPORT_STATUSES = {"pending", "running", "succeeded", "failed"}
MAX_SNAPSHOT_CONVERSATIONS = 500


@dataclass
class CreateReportPayload:
    title: str
    search_type: str
    conversation_ids: list[str]
    query: str | None = None
    filters: dict[str, Any] | None = None
    search_payload: dict[str, Any] | None = None
    ai_scores: dict[str, Any] | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ph1() -> str:
    return "%s" if db._db_mode() == "mysql" else "?"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _row_val(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _connect(db_path: str | None = None) -> Any:
    return db.connect(db_path) if db_path else db.connect()


def _job_to_dict(row: Any, *, include_snapshot: bool = False) -> dict[str, Any]:
    item = {
        "id": _row_val(row, "id"),
        "status": _row_val(row, "status"),
        "title": _row_val(row, "title"),
        "query": _row_val(row, "query"),
        "search_type": _row_val(row, "search_type"),
        "filters": _json_loads(_row_val(row, "filters_json"), {}),
        "search_payload": _json_loads(_row_val(row, "search_payload_json"), {}),
        "sample_count": _row_val(row, "sample_count"),
        "result_markdown": _row_val(row, "result_markdown"),
        "error_message": _row_val(row, "error_message"),
        "created_at": _row_val(row, "created_at"),
        "started_at": _row_val(row, "started_at"),
        "finished_at": _row_val(row, "finished_at"),
    }
    if include_snapshot:
        item["conversation_ids"] = _json_loads(_row_val(row, "conversation_ids_json"), [])
        item["ai_scores"] = _json_loads(_row_val(row, "ai_scores_json"), {})
    return item


def create_report_job(payload: CreateReportPayload, *, db_path: str | None = None) -> dict[str, Any]:
    conversation_ids = [cid for cid in payload.conversation_ids if cid]
    if not conversation_ids:
        raise ValueError("conversation_ids must not be empty")
    if len(conversation_ids) > MAX_SNAPSHOT_CONVERSATIONS:
        raise ValueError(f"conversation_ids must not exceed {MAX_SNAPSHOT_CONVERSATIONS}")

    now_ms = _now_ms()
    job_id = f"report_{uuid.uuid4().hex[:12]}"
    row = {
        "id": job_id,
        "status": "pending",
        "title": payload.title.strip() or "搜索反馈分析报告",
        "query": payload.query,
        "search_type": payload.search_type,
        "filters_json": _json_dumps(payload.filters or {}),
        "search_payload_json": _json_dumps(payload.search_payload or {}),
        "conversation_ids_json": _json_dumps(conversation_ids),
        "ai_scores_json": _json_dumps(payload.ai_scores or {}),
        "sample_count": 0,
        "result_markdown": None,
        "error_message": None,
        "created_at": now_ms,
        "started_at": None,
        "finished_at": None,
    }
    cols = ", ".join(row.keys())
    placeholders = db._ph(len(row))
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO search_report_job ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
    return {"id": job_id, "status": "pending"}


def list_report_jobs(*, limit: int = 20, db_path: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    ph = _ph1()
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM search_report_job ORDER BY created_at DESC, id DESC LIMIT {ph}",
            (limit,),
        ).fetchall()
    return [_job_to_dict(row) for row in rows]


def get_report_job(job_id: str, *, db_path: str | None = None) -> dict[str, Any] | None:
    ph = _ph1()
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT * FROM search_report_job WHERE id = {ph}",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return _job_to_dict(row, include_snapshot=True)
