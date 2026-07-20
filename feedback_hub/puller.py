"""OpenAPI 拉取并直接写库（spec §5.1，重写自 数据采集与打标/pull_openapi.py）。

职责：
- 调用 wetype 反馈 OpenAPI（POST /openapi/search/session）
- 解析 results[i].msgs，过滤 sender==0 && type=='text' && ts 在窗口内
- 直接 upsert 到 feedback 表（conversation_id 占位 'pending'，由打标流水线重算）
- 同步落一份冷备 JSON 到 data/raw/

不依赖旧目录代码；协议字段对齐 spec §3.1 的实测结论。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import urllib3

from feedback_hub import db
from feedback_hub.config import (
    DEFAULT_CHANNEL,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_SERVICE_VID,
    OPENAPI_ENDPOINT,
    RAW_DIR,
    ensure_dirs,
)

urllib3.disable_warnings()

PLATFORM_MAP = {1: "iOS", 2: "Android", 7: "Win", 9: "Mac", 11: "小程序"}


def _platform(ci: dict) -> str:
    if not isinstance(ci, dict):
        return "未知"
    p = ci.get("platform")
    if p in PLATFORM_MAP:
        return PLATFORM_MAP[p]
    dev = (ci.get("device") or "").lower()
    if "iphone" in dev or "ipad" in dev or "ios" in dev:
        return "iOS"
    if "android" in dev:
        return "Android"
    if "windows" in dev or "win64" in dev:
        return "Win"
    if "mac os" in dev or "macintosh" in dev or "macos" in dev:
        return "Mac"
    return f"未知(p={p})"


def fetch_window(start_sec: int, end_sec: int, *,
                 channel: str = DEFAULT_CHANNEL,
                 service_vid: int = DEFAULT_SERVICE_VID,
                 timeout: int = DEFAULT_HTTP_TIMEOUT) -> dict:
    """调一次 /openapi/search/session，返回完整响应 dict。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://wrfeedback.weread.woa.com",
        "referer": "https://wrfeedback.weread.woa.com/search",
        "user-agent": "feedback_hub/1.0",
    }
    body = {
        "serviceAccount.channel": channel,
        "serviceAccount.serviceVid": service_vid,
    }
    params = {"startTime": start_sec, "endTime": end_sec}
    r = requests.post(OPENAPI_ENDPOINT, params=params, json=body,
                      headers=headers, timeout=timeout, verify=False)
    ct = r.headers.get("content-type", "")
    if r.status_code != 200 or "json" not in ct:
        snippet = r.text[:200].replace("\n", " ")
        raise RuntimeError(
            f"接口异常：http={r.status_code} content-type={ct}\n"
            f"前 200 字节：{snippet}\n"
            f"➡️  常见原因：iOA SmartVPN 没开 / DNS 解析失败 / OpenAPI 临时维护"
        )
    return r.json()


def extract_rows(resp: dict, *, start_ms: int, end_ms: int,
                 channel: str) -> tuple[list[dict[str, Any]], dict]:
    """从 OpenAPI 响应中提取本期窗口内的用户文本反馈。"""
    debug = {
        "errCode": resp.get("errCode") if isinstance(resp, dict) else None,
        "session_count": 0, "msg_count": 0,
        "user_msg_count": 0, "in_window_count": 0,
    }
    if not isinstance(resp, dict):
        return [], debug
    sessions = resp.get("results") or []
    debug["session_count"] = len(sessions)

    rows: list[dict[str, Any]] = []
    pulled_at = int(time.time())
    for sw in sessions:
        if not isinstance(sw, dict):
            continue
        sess = sw.get("session") or {}
        for m in sw.get("msgs") or []:
            if not isinstance(m, dict):
                continue
            debug["msg_count"] += 1
            if m.get("sender") != 0 or m.get("type") != "text":
                continue
            debug["user_msg_count"] += 1
            ts = m.get("timestamp") or 0
            if not (start_ms <= ts <= end_ms):
                continue
            debug["in_window_count"] += 1

            content = m.get("content") or {}
            ci = m.get("clientInfo") or {}
            user = m.get("user") or {}
            feedback_id = m.get("feedbackId")
            if not feedback_id:
                continue
            text = (content.get("text") or "").strip()
            tags_list = m.get("tags") or []
            service_vid = sess.get("serviceVid") or DEFAULT_SERVICE_VID
            user_vid = user.get("userVid") or sess.get("userVid")
            external_chat_url = db.build_external_chat_url(channel, service_vid, user_vid)
            raw_extra = {
                "url": content.get("url") or "",
                "scheme": content.get("scheme") or "",
                "device": ci.get("device", ""),
                "raw_platform_code": ci.get("platform"),
                "raw_os_code": ci.get("os"),
                "service_channel": sess.get("channel") or "",
                "service_vid": service_vid,
                "replyId": m.get("replyId"),
            }
            rows.append({
                "feedback_id": feedback_id,
                "conversation_id": "pending",  # 占位，pipeline 重算
                "msg_seq": 0,
                "channel": channel,
                "ts_ms": ts,
                "platform": _platform(ci),
                "appversion": ci.get("appversion", ""),
                "user_vid": user_vid,
                "service_vid": service_vid,
                "external_chat_url": external_chat_url,
                "keyboard_source": ci.get("keyboardSource", ""),
                "device_name": ci.get("deviceName", ""),
                "channelid": ci.get("channelid", ""),
                "enginever": ci.get("enginever", ""),
                "msgtype": "text",
                "text": text,
                "tags": "|".join(str(t) for t in tags_list if t),
                "raw_json": json.dumps(raw_extra, ensure_ascii=False),
                "pulled_at": pulled_at,
            })
    return rows, debug


def write_raw_dump(resp: dict, channel: str, tag: str) -> Path:
    """落一份 OpenAPI 原始响应到 data/raw/，便于 debug。"""
    ensure_dirs()
    p = RAW_DIR / f"raw_{channel}_{tag}.json"
    p.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def upsert_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    commit: bool = True,
) -> int:
    """把 extract_rows 的结果写入 feedback 表，返回新增行数（已存在的跳过）。"""
    inserted = 0
    for row in rows:
        if db.upsert_feedback(conn, row):
            inserted += 1
    if commit:
        conn.commit()
    return inserted


def pull(start_dt: datetime, end_dt: datetime, *,
         channel: str = DEFAULT_CHANNEL,
         service_vid: int = DEFAULT_SERVICE_VID,
         conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """端到端：拉取 → 抽取 → 落 raw → 写库。返回汇总统计。"""
    s_sec, e_sec = int(start_dt.timestamp()), int(end_dt.timestamp())
    s_ms, e_ms = s_sec * 1000, e_sec * 1000
    resp = fetch_window(s_sec, e_sec, channel=channel, service_vid=service_vid)
    rows, debug = extract_rows(resp, start_ms=s_ms, end_ms=e_ms, channel=channel)

    tag = _make_tag(start_dt, end_dt)
    raw_path = write_raw_dump(resp, channel, tag)

    own_conn = False
    if conn is None:
        conn = db.connect()
        db.init_schema(conn)
        own_conn = True
    try:
        for _attempt in range(8):
            try:
                inserted = upsert_rows(conn, rows, commit=False)
                source_generation_ms = db.record_feedback_source_coverage(
                    conn,
                    channel=channel,
                    start_ts_ms=s_ms,
                    end_ts_ms=e_ms,
                )
                break
            except db.SourceGenerationCollisionError:
                conn.rollback()
        else:
            raise RuntimeError("could not allocate a unique source generation")
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()

    return {
        "window": [start_dt.isoformat(), end_dt.isoformat()],
        "fetched_count": len(rows),
        "inserted_count": inserted,
        "skipped_dup_count": len(rows) - inserted,
        "source_generation_ms": source_generation_ms,
        "raw_dump": str(raw_path),
        "debug": debug,
    }


def _make_tag(s: datetime, e: datetime) -> str:
    if s.date() == e.date():
        return f"{s.strftime('%Y%m%d_%H%M%S')}-{e.strftime('%H%M%S')}"
    return f"{s.strftime('%Y%m%d_%H%M%S')}_to_{e.strftime('%Y%m%d_%H%M%S')}"
