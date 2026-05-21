"""签名计算（spec 阶段 2 §2.2 / §2.3 / §2.5）。

签名为 None 表示该会话不参与 Top 5 聚合。
"""
from __future__ import annotations

from typing import Optional, Tuple

from feedback_hub.pusher.config_loader import load


def assign_group(text: str) -> Optional[str]:
    """根据文本命中关键词，返回一个组 ID。

    多组命中规则（spec §2.2）：
      1. 同时命中 specific + generic_bug → 丢弃 generic_bug
      2. 多个 specific 都命中 → 按 priority_order 取第一个
      3. 仅命中 generic_bug → 返回 'generic_bug'
      4. 都没命中 → 返回 None
    """
    if not text:
        return None
    cfg = load()
    groups = cfg["groups"]
    specific_set = set(cfg["specific_groups"])
    priority = cfg["priority_order"]

    hits: set = set()
    for gid, g in groups.items():
        for kw in g["keywords"]:
            if kw and kw in text:
                hits.add(gid)
                break

    # 规则 1：specific + generic 同时命中 → 丢 generic
    if "generic_bug" in hits and (hits & specific_set):
        hits.discard("generic_bug")

    # 规则 2：specific 多命中 → 按优先级取一个
    for g in priority:
        if g in hits:
            return g

    # 规则 3：仅 generic_bug
    if hits == {"generic_bug"}:
        return "generic_bug"

    return None


def compute_signature(
    *,
    full_text: str,
    L2: Optional[str],
    appversion: Optional[str],
) -> Optional[Tuple[str, str, str]]:
    """根据 §2.5 算签名。

    Args:
        full_text:  会话全部用户消息按 msg_seq 升序拼接的文本
        L2:         conversation_label.L2，'|' 分隔字符串
        appversion: conversation_label.appversion

    Returns:
        三元组 (group_id, primary_l2, major_version) 或 None
    """
    group = assign_group(full_text or "")
    if group is None or group == "generic_bug":
        return None

    primary_l2 = (L2.split("|", 1)[0].strip() if L2 else "") or "_unknown"

    if appversion:
        if "." in appversion:
            major_version = appversion.rsplit(".", 1)[0]
        else:
            major_version = appversion
    else:
        major_version = "_unknown"

    return (group, primary_l2, major_version)
