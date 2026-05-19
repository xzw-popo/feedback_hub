"""会话聚合算法（spec §3.2）。

算法核心：
- 同 user_vid 内，按 ts_ms 升序扫描
- 相邻两条间隔 > gap_seconds 时切分新会话
- conversation_id = sha1("{user_vid}:{first_ts_ms}")[:12]，确定性、可重算
- 缺 user_vid 时退化为 anon_<sha1(feedback_id)[:12]>，每条独立成会话

注意：
- 边界比较用 `>`（严格大于）：等于阈值不切，与 spec §3.2 伪代码一致
- 输入消息可乱序；输出**保留输入顺序**，但 msg_seq 与 conversation_id 按时间排序后赋值
- 对消息原 dict 不做破坏性修改：返回新的 dict（拷贝原字段并附加 conversation_id / msg_seq）
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from feedback_hub.config import CONVERSATION_GAP_SECONDS


def make_conversation_id(user_vid: str, first_ts_ms: int) -> str:
    """会话 ID 生成：sha1('{user_vid}:{first_ts_ms}')[:12]"""
    h = hashlib.sha1(f"{user_vid}:{first_ts_ms}".encode("utf-8")).hexdigest()
    return h[:12]


def make_anonymous_user_vid(feedback_id: str) -> str:
    """无 user_vid 时的占位标识：anon_<sha1(feedback_id)[:12]>"""
    h = hashlib.sha1(f"anon:{feedback_id}".encode("utf-8")).hexdigest()
    return f"anon_{h[:12]}"


def _resolve_user_key(msg: dict[str, Any]) -> str:
    """返回该 msg 的"分桶 key"——真实 user_vid 或匿名占位。"""
    uv = msg.get("user_vid")
    if uv:  # 非空字符串
        return str(uv)
    return make_anonymous_user_vid(str(msg.get("feedback_id") or ""))


def assign_conversation_ids(
    messages: list[dict[str, Any]],
    *,
    gap_seconds: int = CONVERSATION_GAP_SECONDS,
) -> list[dict[str, Any]]:
    """对一批消息分配 conversation_id 和 msg_seq。

    Args:
        messages: 每条至少含 feedback_id, user_vid, ts_ms 三个字段（其它字段透传）
        gap_seconds: 切会话的间隔阈值（秒），默认 spec §3.2 的 1800

    Returns:
        新的 list[dict]——保留输入顺序，每条增加 conversation_id / msg_seq 字段
    """
    if not messages:
        return []

    gap_ms = gap_seconds * 1000

    # 按 user_key 分桶
    buckets: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for idx, m in enumerate(messages):
        buckets[_resolve_user_key(m)].append((idx, m))

    # 输出占位（按原 idx 索引），最后按 idx 顺序返回
    out: list[dict[str, Any] | None] = [None] * len(messages)

    for user_key, bucket in buckets.items():
        # 按 ts_ms 升序；ts 相同的按原始 idx 稳定排序
        bucket.sort(key=lambda pair: (pair[1].get("ts_ms") or 0, pair[0]))

        current_conv_id: str | None = None
        prev_ts_ms: int | None = None
        seq = 0

        for idx, m in bucket:
            ts_ms = int(m.get("ts_ms") or 0)
            if prev_ts_ms is None or (ts_ms - prev_ts_ms) > gap_ms:
                current_conv_id = make_conversation_id(user_key, ts_ms)
                seq = 0
            assert current_conv_id is not None
            new_msg = dict(m)
            new_msg["conversation_id"] = current_conv_id
            new_msg["msg_seq"] = seq
            out[idx] = new_msg

            seq += 1
            prev_ts_ms = ts_ms

    return [m for m in out if m is not None]
