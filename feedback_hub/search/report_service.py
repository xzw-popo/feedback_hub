"""Search report persistence and generation helpers."""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from feedback_hub import db
from feedback_hub.search.llm_client import SearchLLMError, chat_completion

REPORT_STATUSES = {"pending", "running", "succeeded", "failed"}
MAX_SNAPSHOT_CONVERSATIONS = 500
MAX_REPORT_SAMPLES = 80
MAX_TEXT_CHARS_PER_CONVERSATION = 500


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


def _update_job(
    job_id: str,
    fields: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    if not fields:
        return
    ph = _ph1()
    assignments = ", ".join(f"{key} = {ph}" for key in fields)
    values = list(fields.values()) + [job_id]
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE search_report_job SET {assignments} WHERE id = {ph}",
            tuple(values),
        )


def mark_report_failed(job_id: str, message: str, *, db_path: str | None = None) -> None:
    _update_job(
        job_id,
        {
            "status": "failed",
            "error_message": message[:500],
            "finished_at": _now_ms(),
        },
        db_path=db_path,
    )


def _fetch_report_samples(
    conversation_ids: list[str],
    ai_scores: dict[str, Any],
    *,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    if not conversation_ids:
        return []

    placeholders = db._ph(len(conversation_ids))
    is_mysql = db._db_mode() == "mysql"
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT f.conversation_id, f.msg_seq, f.text, f.platform, f.appversion, "
            f"cl.L1, cl.L2, cl.severity, cl.last_ts_ms "
            f"FROM feedback f "
            f"LEFT JOIN conversation_label cl ON cl.conversation_id = f.conversation_id "
            f"WHERE f.conversation_id IN ({placeholders}) "
            f"ORDER BY f.conversation_id, f.msg_seq",
            tuple(conversation_ids),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    order = {cid: idx for idx, cid in enumerate(conversation_ids)}
    for row in rows:
        cid = row["conversation_id"] if is_mysql else row[0]
        text = (row["text"] if is_mysql else row[2]) or ""
        sample = grouped.setdefault(
            cid,
            {
                "conversation_id": cid,
                "texts": [],
                "platform": (row["platform"] if is_mysql else row[3]) or "",
                "appversion": (row["appversion"] if is_mysql else row[4]) or "",
                "L1": (row["L1"] if is_mysql else row[5]) or "",
                "L2": (row["L2"] if is_mysql else row[6]) or "",
                "severity": (row["severity"] if is_mysql else row[7]) or "",
                "last_ts_ms": int((row["last_ts_ms"] if is_mysql else row[8]) or 0),
                "ai_score": _extract_ai_score(ai_scores.get(cid)),
                "ai_reason": _extract_ai_reason(ai_scores.get(cid)),
                "snapshot_order": order.get(cid, 999999),
            },
        )
        sample["texts"].append(text)

    samples = []
    for sample in grouped.values():
        sample["text"] = " | ".join(sample.pop("texts"))[:MAX_TEXT_CHARS_PER_CONVERSATION]
        samples.append(sample)

    samples.sort(
        key=lambda item: (
            int(item.get("ai_score") or 0),
            int(item.get("last_ts_ms") or 0),
            -int(item.get("snapshot_order") or 0),
        ),
        reverse=True,
    )
    return samples


def _extract_ai_score(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("score")
    return value if value in (1, 2, 3) else 0


def _extract_ai_reason(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("reason") or "")
    return ""


def _counter(values: list[str]) -> dict[str, int]:
    return dict(Counter(v or "未知" for v in values))


def _build_stats(job: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [int(s["last_ts_ms"]) for s in samples if s.get("last_ts_ms")]
    return {
        "snapshot_count": len(job.get("conversation_ids") or []),
        "sample_count": len(samples),
        "time_range": {
            "from": min(timestamps) if timestamps else None,
            "to": max(timestamps) if timestamps else None,
        },
        "platform": _counter([s.get("platform") or "" for s in samples]),
        "appversion": _counter([s.get("appversion") or "" for s in samples]),
        "severity": _counter([s.get("severity") or "" for s in samples]),
        "L1": _counter([s.get("L1") or "" for s in samples]),
        "L2": _counter([s.get("L2") or "" for s in samples]),
        "ai_score": _counter([str(s.get("ai_score") or "未精筛") for s in samples]),
    }


def _build_report_prompt(job: dict[str, Any], samples: list[dict[str, Any]], stats: dict[str, Any]) -> list[dict[str, str]]:
    sample_text = "\n".join(
        f"- 会话 {s['conversation_id']} | AI相关性 {s.get('ai_score') or '未精筛'} | "
        f"平台 {s.get('platform') or '未知'} | 版本 {s.get('appversion') or '未知'} | "
        f"标签弱参考 {s.get('L1') or '-'} / {s.get('L2') or '-'} / {s.get('severity') or '-'}\n"
        f"  原文：{s.get('text') or ''}"
        for s in samples
    )
    system = (
        "你是一个用户反馈分析报告生成器。请基于本次搜索结果快照生成结构化 Markdown 报告。"
        "不要声称代表全部用户反馈；L1/L2/severity 只是弱参考；关键发现要尽量引用会话 ID 或原文证据。"
    )
    user = (
        f"## 搜索问题\n{job.get('query') or job.get('title')}\n\n"
        f"## 搜索类型\n{job.get('search_type')}\n\n"
        f"## 筛选快照\n{json.dumps(job.get('filters') or {}, ensure_ascii=False)}\n\n"
        f"## 客观统计\n{json.dumps(stats, ensure_ascii=False)}\n\n"
        f"## 代表样本\n{sample_text}\n\n"
        "请严格输出以下 Markdown 结构：\n"
        "# 搜索反馈分析报告\n\n"
        "## 结论摘要\n\n"
        "## 关键发现\n\n"
        "## 主要问题类型\n\n"
        "## 典型用户反馈\n\n"
        "## 数据概览\n\n"
        "## 产品建议\n\n"
        "## 局限性\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run_report_job(job_id: str, *, db_path: str | None = None) -> None:
    job = get_report_job(job_id, db_path=db_path)
    if job is None:
        return
    _update_job(
        job_id,
        {
            "status": "running",
            "error_message": None,
            "started_at": _now_ms(),
            "finished_at": None,
        },
        db_path=db_path,
    )

    try:
        samples = _fetch_report_samples(
            job.get("conversation_ids") or [],
            job.get("ai_scores") or {},
            db_path=db_path,
        )[:MAX_REPORT_SAMPLES]
        if not samples:
            raise SearchLLMError("报告样本为空，请重新搜索后生成")
        stats = _build_stats(job, samples)
        markdown = chat_completion(
            _build_report_prompt(job, samples, stats),
            temperature=0.2,
            max_tokens=8192,
            timeout=180,
        )
        _update_job(
            job_id,
            {
                "status": "succeeded",
                "sample_count": len(samples),
                "result_markdown": markdown,
                "error_message": None,
                "finished_at": _now_ms(),
            },
            db_path=db_path,
        )
    except Exception as e:  # noqa: BLE001 - task boundary records failures for retry
        mark_report_failed(job_id, str(e), db_path=db_path)


def retry_report_job(job_id: str, *, db_path: str | None = None) -> dict[str, Any] | None:
    job = get_report_job(job_id, db_path=db_path)
    if job is None:
        return None
    if job["status"] != "failed":
        raise ValueError("only failed report jobs can be retried")
    _update_job(
        job_id,
        {
            "status": "pending",
            "sample_count": 0,
            "result_markdown": None,
            "error_message": None,
            "started_at": None,
            "finished_at": None,
        },
        db_path=db_path,
    )
    refreshed = get_report_job(job_id, db_path=db_path)
    assert refreshed is not None
    return refreshed
