"""候选 → 企微 Markdown 消息体（spec 阶段 2 §4.1）。

只产出字符串，不发送。webhook.py 负责真正 POST。
"""
from __future__ import annotations

from typing import List

from feedback_hub.pusher import scorer
from feedback_hub.pusher.config_loader import load


def _affected_versions_text(versions: dict) -> str:
    """按计数降序输出 'X.Y.Z (n), A.B.C (m)'。"""
    items = sorted(versions.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{v} ({c})" for v, c in items)


def _severity_label(group: scorer.CandidateGroup) -> str:
    return "[P0]" if group.p0_count > 0 else "[P1]"


def _version_label(group: scorer.CandidateGroup) -> str:
    if group.cross_version:
        return "跨版本"
    return f"{group.signature[2]} 版本"


def _format_one(rank: int, group: scorer.CandidateGroup,
                display_names: dict) -> str:
    sev = _severity_label(group)
    name_cn = display_names.get(group.signature[0], group.signature[0])
    primary_l2 = group.signature[1]
    ver_label = _version_label(group)
    quote = group.representative_text or "（无代表文本）"
    versions = _affected_versions_text(group.affected_versions)
    return (
        f"**{rank}. {sev} {name_cn} · {primary_l2} · {ver_label}** · "
        f"重复 {group.dup_count} 次\n"
        f"> \"{quote}\"\n"
        f"> 涉及版本：{versions}"
    )


def format_message(
    *, push_date: str, scanned: int, all_groups: int,
    top_groups: List[scorer.CandidateGroup],
) -> str:
    """生成完整 Markdown 消息体。

    Args:
        push_date:   'YYYY-MM-DD'
        scanned:     SQL 拉到的候选会话数
        all_groups:  截断前的签名桶数
        top_groups:  Top 5 候选（可能少于 5 个，也可能为空）
    """
    cfg = load()
    display_names = cfg["display_names"]

    if not top_groups:
        return (
            f"## 📊 今日无 Top Bug · {push_date}\n\n"
            f"> 数据窗口：过去 7 天 · 扫描 {scanned} 个 Bug 会话\n\n"
            f"---\n🤖 by feedback_hub"
        )

    header = (
        f"## 📊 今日 Top Bug 候选 · {push_date}\n\n"
        f"> 数据窗口：过去 7 天 · 扫描 {scanned} 个 Bug 会话 · "
        f"命中 {all_groups} 个签名"
    )

    body = "\n\n".join(
        _format_one(i + 1, g, display_names) for i, g in enumerate(top_groups)
    )

    return (
        f"{header}\n\n---\n\n{body}\n\n---\n"
        f"🤖 by feedback_hub · 反馈或建议请联系 @charvel"
    )
