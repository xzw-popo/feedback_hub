"""本地 SQLite → JSON 导出模块（spec §6.2）。

从 SQLite 查询指定日期的增量数据，输出 JSON 文件，
格式与 POST /api/import 请求体一致。

用法：
    python -m feedback_hub.exporter --date 2026-06-12
    python -m feedback_hub.exporter --date 2026-06-12 --output /path/to/export.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from feedback_hub.config import DATA_DIR, DB_PATH
from feedback_hub.db import (
    CONVERSATION_LABEL_COLUMNS,
    FEEDBACK_COLUMNS,
    MESSAGE_LABEL_COLUMNS,
)

BJ_TZ = timezone(timedelta(hours=8))


def _date_to_ms_range(date_str: str) -> tuple[int, int]:
    """将 'YYYY-MM-DD'（北京时间）转为毫秒时间戳范围 [start, end)。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJ_TZ)
    start_ms = int(dt.timestamp() * 1000)
    end_ms = int((dt + timedelta(days=1)).timestamp() * 1000)
    return start_ms, end_ms


def _query_table(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    date_str: str,
    ts_column: str = "ts_ms",
) -> list[dict[str, Any]]:
    """查询指定日期的数据，返回 dict 列表。"""
    start_ms, end_ms = _date_to_ms_range(date_str)
    cols = ", ".join(columns)
    sql = f"SELECT {cols} FROM {table} WHERE {ts_column} >= ? AND {ts_column} < ?"
    cur = conn.execute(sql, (start_ms, end_ms))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def _query_conversation_label(
    conn: sqlite3.Connection,
    date_str: str,
) -> list[dict[str, Any]]:
    """查询指定日期的 conversation_label（按 last_ts_ms 筛选）。"""
    start_ms, end_ms = _date_to_ms_range(date_str)
    cols = ", ".join(CONVERSATION_LABEL_COLUMNS)
    sql = (f"SELECT {cols} FROM conversation_label "
           f"WHERE last_ts_ms >= ? AND last_ts_ms < ?")
    cur = conn.execute(sql, (start_ms, end_ms))
    return [dict(row) for row in cur.fetchall()]


def _query_message_label(
    conn: sqlite3.Connection,
    date_str: str,
) -> list[dict[str, Any]]:
    """查询指定日期的 message_label（通过 JOIN feedback 按 ts_ms 筛选）。"""
    start_ms, end_ms = _date_to_ms_range(date_str)
    cols = ", ".join(f"ml.{c}" for c in MESSAGE_LABEL_COLUMNS)
    sql = (f"SELECT {cols} FROM message_label ml "
           f"JOIN feedback f ON ml.feedback_id = f.feedback_id "
           f"WHERE f.ts_ms >= ? AND f.ts_ms < ?")
    cur = conn.execute(sql, (start_ms, end_ms))
    return [dict(row) for row in cur.fetchall()]


def export_date(date_str: str, db_path: Path | str | None = None) -> dict[str, list[dict[str, Any]]]:
    """导出指定日期的数据，返回 {table: [rows]} 格式。"""
    p = Path(db_path) if db_path is not None else DB_PATH
    if not p.exists():
        raise FileNotFoundError(f"SQLite 文件不存在：{p}")

    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row

    try:
        feedback_rows = _query_table(conn, "feedback", FEEDBACK_COLUMNS, date_str)
        message_label_rows = _query_message_label(conn, date_str)
        conversation_label_rows = _query_conversation_label(conn, date_str)
    finally:
        conn.close()

    return {
        "feedback": feedback_rows,
        "message_label": message_label_rows,
        "conversation_label": conversation_label_rows,
    }


def export_to_file(
    date_str: str,
    output_path: Path | str | None = None,
    db_path: Path | str | None = None,
    import_token: str = "",
) -> Path:
    """导出指定日期的数据到 JSON 文件。返回文件路径。"""
    data = export_date(date_str, db_path)

    # 组装与 /api/import 请求体一致的格式
    payload = {
        "token": import_token,
        "tables": data,
    }

    # 确定输出路径
    if output_path is None:
        export_dir = DATA_DIR / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"{date_str}.json"

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → JSON 导出")
    parser.add_argument("--date", required=True, help="导出日期 YYYY-MM-DD（北京时间）")
    parser.add_argument("--output", default=None, help="输出 JSON 文件路径")
    parser.add_argument("--db-path", default=None, help="SQLite 文件路径")
    parser.add_argument("--token", default="", help="导入 API token（可选，写入 JSON）")
    args = parser.parse_args()

    try:
        out_path = export_to_file(
            date_str=args.date,
            output_path=args.output,
            db_path=args.db_path,
            import_token=args.token,
        )
        data = export_date(args.date, args.db_path)
        for table, rows in data.items():
            print(f"  {table}: {len(rows)} 行")
        print(f"导出完成: {out_path}")
        return 0
    except Exception as e:
        print(f"导出失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
