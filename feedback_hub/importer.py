"""CloudRun 数据导入 API（spec §5）。

端点：
    POST /api/import  —— 批量写入 feedback / message_label / conversation_label

鉴权：
    请求体中的 token 须与环境变量 IMPORT_TOKEN 匹配

处理逻辑：
    - 逐表 UPSERT（INSERT ... ON DUPLICATE KEY UPDATE）
    - 每 500 行一批提交
    - 单表失败不影响其他表
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from feedback_hub import db
from feedback_hub.db import (
    CONVERSATION_LABEL_COLUMNS,
    FEEDBACK_COLUMNS,
    MESSAGE_LABEL_COLUMNS,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- 请求/响应模型 ----------

class TableImportResult(BaseModel):
    upserted: int = 0
    errors: int = 0


class ImportResponse(BaseModel):
    ok: bool
    imported: dict[str, TableImportResult] = {}


class ImportRequest(BaseModel):
    token: str
    tables: dict[str, list[dict[str, Any]]]


# ---------- 批量 UPSERT ----------

BATCH_SIZE = 500


def _batch_upsert_mysql(
    conn: Any, table: str, columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> tuple[int, int]:
    """将一批 dict rows 写入 MySQL（UPSERT），返回 (upserted, errors)。"""
    if not rows:
        return 0, 0

    # 过滤掉不在 columns 中的字段，防止多余 key 导致问题
    clean_rows = [{c: row.get(c) for c in columns} for row in rows]

    cols = ", ".join(columns)
    ph = ", ".join(["%s"] * len(columns))
    update_cols = ", ".join(f"{c}=VALUES({c})" for c in columns)
    sql = (f"INSERT INTO {table} ({cols}) VALUES ({ph}) "
           f"ON DUPLICATE KEY UPDATE {update_cols}")

    upserted = 0
    errors = 0

    for i in range(0, len(clean_rows), BATCH_SIZE):
        batch = clean_rows[i:i + BATCH_SIZE]
        vals = [tuple(row[c] for c in columns) for row in batch]
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, vals)
            conn.commit()
            upserted += len(batch)
        except Exception as e:
            conn.rollback()
            # 尝试逐条插入以定位错误行
            for j, val in enumerate(vals):
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql, val)
                    conn.commit()
                    upserted += 1
                except Exception as e2:
                    conn.rollback()
                    logger.error(f"[import] {table} row {i+j} failed: {e2}")
                    errors += 1
            logger.error(f"[import] {table} batch {i // BATCH_SIZE} partial failure: {e}")

    return upserted, errors


# ---------- 鉴权 ----------

def _verify_token(token: str) -> None:
    """校验导入 token，不匹配则抛 401。"""
    expected = os.environ.get("IMPORT_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="IMPORT_TOKEN 环境变量未配置")
    if token != expected:
        raise HTTPException(status_code=401, detail="token 不匹配")


# ---------- 端点 ----------

# 允许的表名 → 列定义
TABLE_COLUMNS = {
    "feedback": FEEDBACK_COLUMNS,
    "message_label": MESSAGE_LABEL_COLUMNS,
    "conversation_label": CONVERSATION_LABEL_COLUMNS,
}


@router.post("/api/import", response_model=ImportResponse)
def import_data(req: ImportRequest):
    """批量导入数据到 MySQL。"""
    _verify_token(req.token)

    if db._db_mode() != "mysql":
        raise HTTPException(status_code=400, detail="导入 API 仅在 MySQL 模式下可用")

    result = ImportResponse(ok=True, imported={})
    conn = db.connect()

    try:
        for table_name, columns in TABLE_COLUMNS.items():
            rows = req.tables.get(table_name, [])
            if not rows:
                result.imported[table_name] = TableImportResult()
                continue

            upserted, errors = _batch_upsert_mysql(conn._conn, table_name, columns, rows)
            result.imported[table_name] = TableImportResult(upserted=upserted, errors=errors)
            if errors > 0:
                result.ok = False
    except Exception as e:
        logger.error(f"[import] unexpected error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

    return result
