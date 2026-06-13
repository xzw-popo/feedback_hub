"""FastAPI 只读查询服务（spec §6.1 / §6.2）。

5 个端点：
    GET /api/conversations             —— 会话列表（前端默认）
    GET /api/conversations/{conv_id}   —— 单个会话详情（含所有消息）
    GET /api/stats/distribution        —— L1/L2/severity 分布
    GET /api/stats/trend               —— 按天/小时趋势
    GET /api/export.csv                —— CSV 导出（与 /api/conversations 同筛选）

约束：
- 支持 SQLite 和 MySQL 两种后端（通过 DB_MODE 环境变量切换）
- 无鉴权（内网工具）
- 不实现 POST/PATCH（schema 预留 label_history，下一期再做）
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from feedback_hub import db
from feedback_hub.config import L1_VALUES, L2_VALUES, SEVERITY_VALUES
from feedback_hub.importer import router as import_router


def _ph1() -> str:
    """单参数占位符。"""
    return "%s" if db._db_mode() == "mysql" else "?"


def _ph_n(n: int) -> str:
    """多参数占位符列表。"""
    return db._ph(n)


def _parse_dt(s: Optional[str], *, end_of_day: bool = False) -> Optional[int]:
    """把 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS' 转成 unix 毫秒。返回 None 表示未指定。"""
    if not s:
        return None
    s = s.strip()
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d")
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d" and end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"无法解析时间：{s}")


def _l2_split(s: Optional[str]) -> list[str]:
    if not s:
        return []
    return [x for x in s.split("|") if x]


def _row_val(row: Any, key: str) -> Any:
    """兼容 sqlite3.Row 和 pymysql DictCursor 的字段读取。"""
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _conv_to_item(r: Any, preview_text: str) -> dict:
    return {
        "conversation_id": _row_val(r, "conversation_id"),
        "L1": _row_val(r, "L1"),
        "L2": _l2_split(_row_val(r, "L2")),
        "severity": _row_val(r, "severity"),
        "confidence": _row_val(r, "confidence"),
        "reason": _row_val(r, "reason"),
        "msg_count": _row_val(r, "msg_count"),
        "first_ts_ms": _row_val(r, "first_ts_ms"),
        "last_ts_ms": _row_val(r, "last_ts_ms"),
        "user_vid": _row_val(r, "user_vid"),
        "appversion": _row_val(r, "appversion"),
        "channel": _row_val(r, "channel"),
        "preview_text": preview_text,
    }


def _get_cors_origins() -> list[str]:
    """从环境变量 CORS_ORIGINS 读取允许的 Origin 列表。

    格式：逗号分隔，如 "http://localhost:5173,https://example.com"
    未设置时回退到本地开发默认值。
    """
    env_val = os.environ.get("CORS_ORIGINS", "")
    if env_val.strip():
        return [o.strip() for o in env_val.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def create_app(db_path: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="feedback_hub", version="1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_get_cors_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    # 注册导入 API（POST /api/import）
    app.include_router(import_router)

    def _conn() -> Any:
        c = db.connect(db_path) if db_path else db.connect()
        return c

    def _build_filters(
        from_: Optional[str], to: Optional[str],
        L1: Optional[str], L2: Optional[str],
        severity: Optional[str], q: Optional[str],
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        ph = _ph1()
        f_ms = _parse_dt(from_)
        t_ms = _parse_dt(to, end_of_day=True)
        if f_ms is not None:
            clauses.append(f"cl.last_ts_ms >= {ph}")
            params.append(f_ms)
        if t_ms is not None:
            clauses.append(f"cl.last_ts_ms <= {ph}")
            params.append(t_ms)
        if L1:
            if L1 not in L1_VALUES:
                raise HTTPException(status_code=400, detail=f"非法 L1：{L1}")
            clauses.append(f"cl.L1 = {ph}")
            params.append(L1)
        if L2:
            if L2 not in L2_VALUES:
                raise HTTPException(status_code=400, detail=f"非法 L2：{L2}")
            clauses.append(f"cl.L2 LIKE {ph}")
            params.append(f"%{L2}%")
        if severity:
            if severity not in SEVERITY_VALUES:
                raise HTTPException(status_code=400, detail=f"非法 severity：{severity}")
            clauses.append(f"cl.severity = {ph}")
            params.append(severity)
        if q:
            clauses.append(f"cl.conversation_id IN ("
                           f"SELECT DISTINCT conversation_id FROM feedback WHERE text LIKE {ph})")
            params.append(f"%{q}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _query_conversations(
        from_, to, L1, L2, severity, q, limit, offset,
    ) -> tuple[int, list[Any]]:
        where, params = _build_filters(from_, to, L1, L2, severity, q)
        is_mysql = db._db_mode() == "mysql"
        with _conn() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM conversation_label cl {where}", params
            ).fetchone()
            total = total_row["cnt"] if is_mysql else total_row[0]

            ph = _ph1()
            rows = conn.execute(
                f"SELECT cl.* FROM conversation_label cl {where} "
                f"ORDER BY cl.last_ts_ms DESC LIMIT {ph} OFFSET {ph}",
                params + [limit, offset],
            ).fetchall()
            preview_map: dict[str, str] = {}
            if rows:
                ids = [_row_val(r, "conversation_id") for r in rows]
                placeholders = _ph_n(len(ids))
                cur = conn.execute(
                    f"SELECT conversation_id, text FROM feedback "
                    f"WHERE conversation_id IN ({placeholders}) AND msg_seq = 0",
                    tuple(ids),
                )
                for r in cur.fetchall():
                    cid = r["conversation_id"] if is_mysql else r[0]
                    txt = (r["text"] if is_mysql else r[1]) or ""
                    preview_map[cid] = txt[:80]
            items = [_conv_to_item(r, preview_map.get(_row_val(r, "conversation_id"), "")) for r in rows]
        return total, items

    @app.get("/api/conversations")
    def list_conversations(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        L1: Optional[str] = None,
        L2: Optional[str] = None,
        severity: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        if not (1 <= limit <= 500):
            raise HTTPException(status_code=400, detail="limit 必须在 [1, 500]")
        if offset < 0:
            raise HTTPException(status_code=400, detail="offset 不能为负")
        total, items = _query_conversations(from_, to, L1, L2, severity, q, limit, offset)
        return {"total": total, "items": items}

    @app.get("/api/conversations/{conv_id}")
    def get_conversation(conv_id: str):
        ph = _ph1()
        with _conn() as conn:
            cl = conn.execute(
                f"SELECT * FROM conversation_label WHERE conversation_id = {ph}",
                (conv_id,),
            ).fetchone()
            if cl is None:
                raise HTTPException(status_code=404, detail="conversation not found")
            msg_rows = conn.execute(
                f"SELECT f.feedback_id, f.msg_seq, f.ts_ms, f.text, f.appversion, f.platform, "
                f"ml.L1, ml.L2, ml.severity, ml.confidence, ml.reason, ml.source, ml.rule_name "
                f"FROM feedback f LEFT JOIN message_label ml ON f.feedback_id = ml.feedback_id "
                f"WHERE f.conversation_id = {ph} ORDER BY f.msg_seq ASC",
                (conv_id,),
            ).fetchall()
        return {
            "conversation": _conv_to_item(cl, ""),
            "messages": [{
                "feedback_id": _row_val(r, "feedback_id"),
                "msg_seq": _row_val(r, "msg_seq"),
                "ts_ms": _row_val(r, "ts_ms"),
                "text": _row_val(r, "text"),
                "appversion": _row_val(r, "appversion"),
                "platform": _row_val(r, "platform"),
                "L1": _row_val(r, "L1"),
                "L2": _l2_split(_row_val(r, "L2")),
                "severity": _row_val(r, "severity"),
                "confidence": _row_val(r, "confidence"),
                "reason": _row_val(r, "reason"),
                "source": _row_val(r, "source"),
                "rule_name": _row_val(r, "rule_name"),
            } for r in msg_rows],
        }

    @app.get("/api/stats/distribution")
    def stats_distribution(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
    ):
        where, params = _build_filters(from_, to, None, None, None, None)
        is_mysql = db._db_mode() == "mysql"
        with _conn() as conn:
            l1_rows = conn.execute(
                f"SELECT cl.L1, COUNT(*) AS cnt FROM conversation_label cl {where} GROUP BY cl.L1",
                params,
            ).fetchall()
            l1 = {_row_val(r, "L1"): _row_val(r, "cnt") for r in l1_rows}

            sev_rows = conn.execute(
                f"SELECT cl.severity, COUNT(*) AS cnt FROM conversation_label cl {where} GROUP BY cl.severity",
                params,
            ).fetchall()
            sev = {_row_val(r, "severity"): _row_val(r, "cnt") for r in sev_rows}

            # L2 union 需要拆 '|' —— 简单做法：取所有 L2 列，python 端展开
            l2_cnt: dict[str, int] = {}
            cur = conn.execute(
                f"SELECT cl.L2 FROM conversation_label cl {where}", params
            )
            for r in cur.fetchall():
                l2_str = r["L2"] if is_mysql else r[0]
                for tag in _l2_split(l2_str):
                    l2_cnt[tag] = l2_cnt.get(tag, 0) + 1
        return {"L1": l1, "L2": l2_cnt, "severity": sev}

    @app.get("/api/stats/trend")
    def stats_trend(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        granularity: str = "day",
    ):
        if granularity not in {"hour", "day"}:
            raise HTTPException(status_code=400, detail="granularity 仅支持 hour|day")
        fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
        where, params = _build_filters(from_, to, None, None, None, None)
        is_mysql = db._db_mode() == "mysql"
        with _conn() as conn:
            cur = conn.execute(
                f"SELECT cl.last_ts_ms, cl.L1 FROM conversation_label cl {where} "
                f"ORDER BY cl.last_ts_ms ASC", params,
            )
            buckets: dict[str, dict[str, int]] = {}
            for r in cur.fetchall():
                ts_ms = r["last_ts_ms"] if is_mysql else r[0]
                l1 = r["L1"] if is_mysql else r[1]
                bucket = datetime.fromtimestamp(ts_ms / 1000).strftime(fmt)
                buckets.setdefault(bucket, {})
                buckets[bucket][l1] = buckets[bucket].get(l1, 0) + 1
        return {
            "granularity": granularity,
            "buckets": [{"bucket": k, "counts": v} for k, v in sorted(buckets.items())],
        }

    @app.get("/api/export.csv")
    def export_csv(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        L1: Optional[str] = None,
        L2: Optional[str] = None,
        severity: Optional[str] = None,
        q: Optional[str] = None,
    ):
        # 直接走与 list_conversations 同样的筛选，不分页（最多 5000 条防爆）
        _, items = _query_conversations(from_, to, L1, L2, severity, q, limit=5000, offset=0)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([
            "conversation_id", "L1", "L2", "severity", "confidence", "reason",
            "msg_count", "first_ts_human", "last_ts_human",
            "user_vid", "appversion", "channel", "preview_text",
        ])
        for it in items:
            w.writerow([
                it["conversation_id"], it["L1"], "|".join(it["L2"]), it["severity"],
                it["confidence"], it["reason"], it["msg_count"],
                datetime.fromtimestamp(it["first_ts_ms"] / 1000).isoformat(),
                datetime.fromtimestamp(it["last_ts_ms"] / 1000).isoformat(),
                it["user_vid"] or "", it["appversion"] or "", it["channel"], it["preview_text"],
            ])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="conversations.csv"'},
        )

    return app


# 便于 uvicorn 直接加载 `feedback_hub.api:app`
app = create_app()
