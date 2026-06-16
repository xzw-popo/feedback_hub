"""智能搜索 API 端点：关键词搜索、AI 搜索、AI 精筛。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from feedback_hub import config, db
from feedback_hub.search.llm_client import (
    SearchLLMError,
    generate_regex_patterns,
    score_relevance,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])


# ---------------------------------------------------------------------------
# Pydantic 请求模型
# ---------------------------------------------------------------------------

class MetadataFilters(BaseModel):
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    L1: Optional[str] = None
    L2: Optional[str] = None
    severity: Optional[str] = None
    platform: Optional[str] = None
    appversion: Optional[str] = None
    device_name: Optional[str] = None

    model_config = {"populate_by_name": True}


class ParsedSearchIntent(BaseModel):
    text_query: str
    filters: MetadataFilters


class KeywordGroup(BaseModel):
    keywords: list[str]
    logic: str = "AND"  # AND | OR


class KeywordSearchRequest(BaseModel):
    groups: list[KeywordGroup] = []
    excludes: list[str] = []
    filters: MetadataFilters = MetadataFilters()
    limit: int = Field(200, ge=1, le=500)
    offset: int = Field(0, ge=0)


class SmartSearchRequest(BaseModel):
    query: str
    filters: MetadataFilters = MetadataFilters()
    limit: int = Field(200, ge=1, le=500)
    offset: int = Field(0, ge=0)


class FineFilterRequest(BaseModel):
    query: str
    conversation_ids: list[str]
    batch_size: int = Field(config.FINE_FILTER_BATCH_SIZE, ge=1, le=20)


# ---------------------------------------------------------------------------
# 共享工具函数
# ---------------------------------------------------------------------------

def _ph1() -> str:
    return "%s" if db._db_mode() == "mysql" else "?"


def _row_val(row: Any, key: str) -> Any:
    """兼容 sqlite3.Row 和 pymysql DictCursor 的字段读取。"""
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _l2_split(s: Optional[str]) -> list[str]:
    if not s:
        return []
    return [x for x in s.split("|") if x]


def _conv_to_item(r: Any, preview_text: str, platform: str = "") -> dict:
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
        "platform": platform,
        "preview_text": preview_text,
    }


def _parse_dt(s: Optional[str], *, end_of_day: bool = False) -> Optional[int]:
    """把 'YYYY-MM-DD' 转成 unix 毫秒。"""
    from datetime import datetime
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d" and end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])v?(\d+(?:\.\d+){1,4})(?![A-Za-z0-9])", re.IGNORECASE)
_PLATFORM_ALIASES: tuple[tuple[str, str], ...] = (
    (r"(?<![A-Za-z0-9])windows?(?![A-Za-z0-9])", "Win"),
    (r"(?<![A-Za-z0-9])win(?:32|64)?(?![A-Za-z0-9])", "Win"),
    (r"(?<![A-Za-z0-9])mac\s*os(?![A-Za-z0-9])", "Mac"),
    (r"(?<![A-Za-z0-9])macos(?![A-Za-z0-9])", "Mac"),
    (r"(?<![A-Za-z0-9])mac(?![A-Za-z0-9])", "Mac"),
    (r"(?<![A-Za-z0-9])ios(?![A-Za-z0-9])", "iOS"),
    (r"(?<![A-Za-z0-9])android(?![A-Za-z0-9])", "Android"),
    (r"小程序", "小程序"),
)
_DEVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z0-9])iPhone\s*\d+(?:,\d+)?(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])iPad\s*\d*(?:,\d+)?(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])MacBook(?:\s*(?:Pro|Air))?(?:\s*\d+(?:,\d+)?)?(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])Mate\s*\d+\w*(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])Pixel\s*\d+\w*(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])ThinkPad\s+[A-Za-z0-9][A-Za-z0-9 -]{0,24}(?![A-Za-z0-9])", re.IGNORECASE),
)


def _append_csv(existing: Optional[str], values: list[str]) -> Optional[str]:
    merged: list[str] = []
    for v in (existing or "").split(","):
        v = v.strip()
        if v and v not in merged:
            merged.append(v)
    for v in values:
        v = v.strip()
        if v and v not in merged:
            merged.append(v)
    return ",".join(merged) if merged else None


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars)


def _normalize_text_query(query: str) -> str:
    query = re.sub(r"\s+", " ", query).strip()
    return query.strip() or "反馈"


def _parse_structured_intent(query: str, filters: MetadataFilters) -> ParsedSearchIntent:
    """从自然语言查询中提取版本、平台、设备，并从正文意图中剥离这些片段。"""
    spans: list[tuple[int, int]] = []
    versions: list[str] = []
    platforms: list[str] = []
    devices: list[str] = []

    for m in _VERSION_RE.finditer(query):
        versions.append(m.group(1))
        spans.append(m.span())

    for pattern, platform in _PLATFORM_ALIASES:
        for m in re.finditer(pattern, query, re.IGNORECASE):
            platforms.append(platform)
            spans.append(m.span())

    for pattern in _DEVICE_PATTERNS:
        for m in pattern.finditer(query):
            devices.append(re.sub(r"\s+", "", m.group(0)))
            spans.append(m.span())

    text_query = _normalize_text_query(_blank_spans(query, spans))
    merged_filters = filters.model_copy(update={
        "appversion": _append_csv(filters.appversion, versions),
        "platform": _append_csv(filters.platform, platforms),
        "device_name": _append_csv(filters.device_name, devices),
    })
    return ParsedSearchIntent(text_query=text_query, filters=merged_filters)


def _build_metadata_where(filters: MetadataFilters) -> tuple[str, list[Any]]:
    """从元数据筛选构建 WHERE 子句。"""
    clauses: list[str] = []
    params: list[Any] = []
    ph = _ph1()

    f_ms = _parse_dt(filters.from_)
    t_ms = _parse_dt(filters.to, end_of_day=True)
    if f_ms is not None:
        clauses.append(f"cl.last_ts_ms >= {ph}")
        params.append(f_ms)
    if t_ms is not None:
        clauses.append(f"cl.last_ts_ms <= {ph}")
        params.append(t_ms)
    if filters.L1:
        if filters.L1 not in config.L1_VALUES:
            raise HTTPException(status_code=400, detail=f"非法 L1：{filters.L1}")
        clauses.append(f"cl.L1 = {ph}")
        params.append(filters.L1)
    if filters.L2:
        if filters.L2 not in config.L2_VALUES:
            raise HTTPException(status_code=400, detail=f"非法 L2：{filters.L2}")
        clauses.append(f"cl.L2 LIKE {ph}")
        params.append(f"%{filters.L2}%")
    if filters.severity:
        if filters.severity not in config.SEVERITY_VALUES:
            raise HTTPException(status_code=400, detail=f"非法 severity：{filters.severity}")
        clauses.append(f"cl.severity = {ph}")
        params.append(filters.severity)
    if filters.platform:
        # 逗号分隔多选，如 "iOS,Android"
        plat_list = [p.strip() for p in filters.platform.split(",") if p.strip()]
        if plat_list:
            plat_ph = db._ph(len(plat_list))
            clauses.append(
                f"cl.conversation_id IN ("
                f"SELECT DISTINCT conversation_id FROM feedback WHERE platform IN ({plat_ph}))"
            )
            params.extend(plat_list)
    if filters.appversion:
        versions = [v.strip() for v in filters.appversion.split(",") if v.strip()]
        if versions:
            version_parts = [f"cl.appversion LIKE {ph}" for _ in versions]
            clauses.append(f"({' OR '.join(version_parts)})")
            params.extend(f"{v}%" for v in versions)
    if filters.device_name:
        devices = [d.strip().lower() for d in filters.device_name.split(",") if d.strip()]
        if devices:
            device_parts = [f"LOWER(device_name) LIKE {ph}" for _ in devices]
            clauses.append(
                f"cl.conversation_id IN ("
                f"SELECT DISTINCT conversation_id FROM feedback WHERE {' OR '.join(device_parts)})"
            )
            params.extend(f"%{d}%" for d in devices)

    return (" AND ".join(clauses)), params


def _execute_search_query(
    search_where: str,
    search_params: list[Any],
    filters: MetadataFilters,
    limit: int,
    offset: int,
) -> tuple[int, list[dict]]:
    """执行搜索查询，返回 (total, items)。"""
    meta_where, meta_params = _build_metadata_where(filters)

    # 组合 WHERE
    parts = []
    all_params = []
    if search_where:
        parts.append(f"({search_where})")
        all_params.extend(search_params)
    if meta_where:
        parts.append(f"({meta_where})")
        all_params.extend(meta_params)
    where_clause = ("WHERE " + " AND ".join(parts)) if parts else ""

    is_mysql = db._db_mode() == "mysql"
    ph = _ph1()

    with db.connect() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM conversation_label cl {where_clause}",
            tuple(all_params),
        ).fetchone()
        total = total_row["cnt"] if is_mysql else total_row[0]

        rows = conn.execute(
            f"SELECT cl.* FROM conversation_label cl {where_clause} "
            f"ORDER BY cl.last_ts_ms DESC LIMIT {ph} OFFSET {ph}",
            tuple(all_params) + (limit, offset),
        ).fetchall()

        # 取 preview_text 和 platform
        preview_map: dict[str, str] = {}
        platform_map: dict[str, str] = {}
        if rows:
            ids = [_row_val(r, "conversation_id") for r in rows]
            placeholders = db._ph(len(ids))
            cur = conn.execute(
                f"SELECT conversation_id, text, platform FROM feedback "
                f"WHERE conversation_id IN ({placeholders}) AND msg_seq = 0",
                tuple(ids),
            )
            for r in cur.fetchall():
                cid = r["conversation_id"] if is_mysql else r[0]
                txt = (r["text"] if is_mysql else r[1]) or ""
                plat = (r["platform"] if is_mysql else r[2]) or ""
                preview_map[cid] = txt[:80]
                platform_map[cid] = plat

        items = [
            _conv_to_item(
                r,
                preview_map.get(_row_val(r, "conversation_id"), ""),
                platform_map.get(_row_val(r, "conversation_id"), ""),
            )
            for r in rows
        ]

    return total, items


# ---------------------------------------------------------------------------
# 关键词搜索 SQL 构建
# ---------------------------------------------------------------------------

def _build_keyword_where(
    groups: list[KeywordGroup],
    excludes: list[str],
) -> tuple[str, list[Any]]:
    """将关键词构建器条件翻译为 SQL WHERE 子句。

    关键词：conversation_id IN (SELECT ... WHERE text LIKE '%kw%')
    排除词：conversation_id NOT IN (SELECT ... WHERE text LIKE '%ex%')

    关键词和排除词使用独立的子查询，因为：
    - 关键词 IN 子查询：找「至少有一条消息包含关键词的会话」
    - 排除词 NOT IN 子查询：找「没有任何消息包含排除词的会话」
    若把 NOT LIKE 放在同一个 IN 子查询里，语义会变成
    「至少有一条不含排除词的消息的会话」，几乎等于所有会话。
    """
    all_params: list[Any] = []
    where_parts: list[str] = []

    # --- 关键词 IN 子查询 ---
    group_clauses: list[str] = []
    for group in groups:
        if not group.keywords:
            continue
        inner_parts = []
        for kw in group.keywords:
            inner_parts.append(f"text LIKE {_ph1()}")
            all_params.append(f"%{kw}%")
        if group.logic == "AND":
            inner = " AND ".join(inner_parts)
        else:
            inner = " OR ".join(inner_parts)
        group_clauses.append(f"({inner})")

    if group_clauses:
        inner_where = " OR ".join(group_clauses)
        where_parts.append(
            f"cl.conversation_id IN (SELECT DISTINCT conversation_id FROM feedback WHERE {inner_where})"
        )

    # --- 排除词 NOT IN 子查询 ---
    if excludes:
        exclude_parts = [f"text LIKE {_ph1()}" for _ in excludes]
        for ex in excludes:
            all_params.append(f"%{ex}%")
        exclude_inner = " OR ".join(exclude_parts)
        where_parts.append(
            f"cl.conversation_id NOT IN (SELECT DISTINCT conversation_id FROM feedback WHERE {exclude_inner})"
        )

    if not where_parts:
        return "", []

    where = " AND ".join(where_parts)
    return where, all_params


# ---------------------------------------------------------------------------
# AI 搜索 SQL 构建
# ---------------------------------------------------------------------------

def _build_regex_where(
    group_patterns: list[list[str]],
    group_logics: list[str],
    group_logic: str,
) -> tuple[str, list[Any]]:
    """将 LLM 生成的分组正则列表翻译为 SQL WHERE 子句。

    group_patterns: 每组的正则列表
    group_logics: 每组内部的逻辑 (AND/OR)
    group_logic: 组间逻辑 (AND/OR)
    """
    if not group_patterns:
        return "", []

    group_clauses = []
    all_params: list[Any] = []

    for patterns, logic in zip(group_patterns, group_logics):
        parts = []
        for p in patterns:
            parts.append(f"text REGEXP {_ph1()}")
            all_params.append(p)
        joiner = " AND " if logic == "AND" else " OR "
        group_clauses.append(f"({joiner.join(parts)})")

    group_joiner = " AND " if group_logic == "AND" else " OR "
    inner_where = group_joiner.join(group_clauses)
    # length 限制：超长文本（崩溃日志/直播口水等）不参与正则匹配，
    # 否则 LLM 生成的含 .* 正则在 5万+字符上会触发回溯爆炸（实测卡死 80s+）。
    where = (
        f"cl.conversation_id IN ("
        f"SELECT DISTINCT conversation_id FROM feedback "
        f"WHERE length(text) < {config.SEARCH_REGEXP_MAX_TEXT_LEN} AND ({inner_where}))"
    )
    return where, all_params


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

@router.post("/keyword-search")
def keyword_search(req: KeywordSearchRequest):
    """关键词构建器搜索。"""
    # 校验：至少有一组包含关键词（排除词是可选的附加过滤）
    has_any_keyword = any(g.keywords for g in req.groups)
    if not has_any_keyword:
        raise HTTPException(status_code=400, detail="请至少输入一个关键词")

    # 校验关键词组逻辑
    for g in req.groups:
        if g.logic not in ("AND", "OR"):
            raise HTTPException(status_code=400, detail=f"非法逻辑：{g.logic}")

    search_where, search_params = _build_keyword_where(req.groups, req.excludes)
    total, items = _execute_search_query(
        search_where, search_params, req.filters, req.limit, req.offset,
    )
    return {"total": total, "items": items}


@router.post("/smart-search")
def smart_search(req: SmartSearchRequest):
    """AI 智能搜索：自然语言 → LLM → 分组正则 → SQL。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="请输入搜索意图")

    parsed = _parse_structured_intent(req.query, req.filters)
    fallback_mode: str | None = None

    try:
        group_patterns, group_logics, group_logic = generate_regex_patterns(parsed.text_query)
    except SearchLLMError as e:
        logger.warning("Smart search LLM failed: %s", e)
        group_patterns, group_logics, group_logic = [], [], "OR"
        fallback_mode = "query_keywords"

    search_where, search_params = _build_regex_where(group_patterns, group_logics, group_logic)
    if not search_where:
        # LLM 失败或生成的正则全部无效，回退为 LIKE。
        all_patterns = [p for grp in group_patterns for p in grp]
        keywords = re_fallback_keywords(all_patterns) or re_fallback_keywords([parsed.text_query])
        if keywords:
            like_parts = []
            like_params = []
            for kw in keywords:
                like_parts.append(f"text LIKE {_ph1()}")
                like_params.append(f"%{kw}%")
            inner = " OR ".join(like_parts)
            search_where = f"cl.conversation_id IN (SELECT DISTINCT conversation_id FROM feedback WHERE {inner})"
            search_params = like_params
            fallback_mode = fallback_mode or "regex_keywords"
        else:
            raise HTTPException(status_code=503, detail="AI 未能生成有效搜索条件")

    total, items = _execute_search_query(
        search_where, search_params, parsed.filters, req.limit, req.offset,
    )

    # 展平 patterns 供 debug 展示
    flat_patterns = [p for grp in group_patterns for p in grp]

    return {
        "total": total,
        "items": items,
        "debug": {
            "regex_groups": [
                {"patterns": grp, "logic": logic}
                for grp, logic in zip(group_patterns, group_logics)
            ],
            "group_logic": group_logic,
            "regex_patterns": flat_patterns,  # 向后兼容
            "text_query": parsed.text_query,
            "metadata_filters": parsed.filters.model_dump(by_alias=True),
            "fallback": fallback_mode,
        },
    }


import re as _re

def re_fallback_keywords(patterns: list[str]) -> list[str]:
    """从无效正则中提取纯文本关键词作为回退。"""
    keywords = []
    for p in patterns:
        # 提取中文字符序列和英文单词
        found = _re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+", p)
        keywords.extend(found)
    return keywords[:5]  # 最多 5 个


# ---------------------------------------------------------------------------
# AI 精筛（SSE 流式）
# ---------------------------------------------------------------------------

@router.post("/fine-filter")
async def fine_filter(req: FineFilterRequest):
    """AI 精筛：对初筛结果并发 LLM 评分，SSE 流式返回。

    自适应 batch_size：
    - 条数 ≤ 50：batch_size=1，全并发
    - 条数 51~200：batch_size=2
    - 条数 > 200：batch_size=5
    信号量 = FINE_FILTER_MAX_CONCURRENCY，直接控制并发 LLM 调用数。
    """
    if not req.conversation_ids:
        raise HTTPException(status_code=400, detail="请提供待精筛的会话列表")
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="请提供搜索意图")

    if len(req.conversation_ids) > config.FINE_FILTER_MAX_CONVERSATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"精筛上限 {config.FINE_FILTER_MAX_CONVERSATIONS} 条，请缩小范围",
        )

    # 自适应 batch_size
    total_count = len(req.conversation_ids)
    if total_count <= 50:
        adaptive_batch_size = 1
    elif total_count <= 200:
        adaptive_batch_size = 2
    else:
        adaptive_batch_size = 5
    # 用户显式指定 batch_size 时仍可覆盖（仅当 > 1 时）
    batch_size = max(adaptive_batch_size, req.batch_size) if req.batch_size > 1 else adaptive_batch_size

    async def event_generator():
        # 1. 从 DB 取所有会话的反馈文本
        feedback_map = _fetch_feedback_texts(req.conversation_ids)

        # 2. 按批次分组
        conversation_ids = list(feedback_map.keys())
        batches = [
            conversation_ids[i:i + batch_size]
            for i in range(0, len(conversation_ids), batch_size)
        ]

        total_processed = 0

        # 3. 信号量直接控制并发 LLM 调用数
        semaphore = asyncio.Semaphore(config.FINE_FILTER_MAX_CONCURRENCY)

        async def score_batch(batch_ids: list[str]) -> list[dict]:
            feedbacks = []
            for cid in batch_ids:
                text = feedback_map.get(cid, "")
                feedbacks.append({"id": cid, "text": text})

            # 在线程池中运行同步的 LLM 调用
            loop = asyncio.get_event_loop()
            try:
                results = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: score_relevance(req.query, feedbacks, timeout=config.FINE_FILTER_BATCH_TIMEOUT),
                    ),
                    timeout=config.FINE_FILTER_BATCH_TIMEOUT + 10,
                )
            except (asyncio.TimeoutError, SearchLLMError) as e:
                logger.warning("Fine filter batch failed: %s", e)
                # 超时或错误，返回默认评分
                results = [{"id": f["id"], "score": 2, "reason": "timeout"} for f in feedbacks]

            # 映射 id：results 里的 id 可能是序号，需要用 feedbacks 的 id
            mapped = []
            for i, fb in enumerate(feedbacks):
                r = results[i] if i < len(results) else {"score": 2, "reason": "missing"}
                mapped.append({
                    "id": fb["id"],
                    "score": r.get("score", 2),
                    "reason": r.get("reason", ""),
                })
            return mapped

        # 4. 创建所有批次任务
        tasks = []
        for batch in batches:
            async def _run(b=batch):
                async with semaphore:
                    return await score_batch(b)
            tasks.append(_run())

        # 5. 带整体超时的逐批推送
        try:
            for coro in asyncio.as_completed(tasks, timeout=config.FINE_FILTER_TOTAL_TIMEOUT):
                try:
                    batch_results = await coro
                    total_processed += len(batch_results)
                    yield {
                        "event": "batch",
                        "data": json.dumps(
                            {"batch": batch_results, "processed": total_processed},
                            ensure_ascii=False,
                        ),
                    }
                except Exception as e:
                    logger.error("Fine filter batch error: %s", e)
                    continue
        except asyncio.TimeoutError:
            logger.warning("Fine filter total timeout (%ds), returning partial results", config.FINE_FILTER_TOTAL_TIMEOUT)
            yield {
                "event": "warning",
                "data": json.dumps({"message": f"精筛超时（{config.FINE_FILTER_TOTAL_TIMEOUT}s），结果可能不完整"}),
            }

        yield {
            "event": "done",
            "data": json.dumps({"done": True, "total_processed": total_processed}),
        }

    return EventSourceResponse(event_generator())


def _fetch_feedback_texts(conversation_ids: list[str]) -> dict[str, str]:
    """从 DB 取每个会话的所有反馈文本，拼接为一条。"""
    if not conversation_ids:
        return {}

    is_mysql = db._db_mode() == "mysql"
    placeholders = db._ph(len(conversation_ids))

    with db.connect() as conn:
        cur = conn.execute(
            f"SELECT conversation_id, text FROM feedback "
            f"WHERE conversation_id IN ({placeholders}) "
            f"ORDER BY conversation_id, msg_seq",
            tuple(conversation_ids),
        )
        rows = cur.fetchall()

    # 拼接每个会话的所有文本
    result: dict[str, list[str]] = {}
    for r in rows:
        cid = r["conversation_id"] if is_mysql else r[0]
        txt = (r["text"] if is_mysql else r[1]) or ""
        result.setdefault(cid, []).append(txt)

    # 拼成单条，限制长度
    return {
        cid: " | ".join(texts)[:500]
        for cid, texts in result.items()
    }
