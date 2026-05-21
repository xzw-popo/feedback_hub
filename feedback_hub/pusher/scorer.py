"""聚合打分 + Top 5 选取（spec 阶段 2 §3.3 / §3.4 / §3.5）。

纯函数模块，无 IO。SQL 端的拼接结果由 candidate.py 喂进来。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from feedback_hub.pusher.signature import compute_signature

ONE_DAY_MS = 86_400_000


@dataclass
class ConversationRow:
    """一条候选会话的扁平视图（candidate.py 的 SQL 输出）。"""
    conversation_id: str
    L1: str
    L2: str
    severity: str
    confidence: float
    appversion: Optional[str]
    last_ts_ms: int
    full_text: str               # 签名匹配用：全会话拼接
    top_conf_text: str           # 展示用：confidence 最高消息原文
    top_conf_feedback_id: str


@dataclass
class CandidateGroup:
    """同签名的一组会话 + 评分元数据。"""
    signature: Tuple[str, str, str]
    conversations: List[ConversationRow]
    score: float = 0.0
    dup_count: int = 0
    p0_count: int = 0
    cross_version: int = 0
    recent_24h: int = 0
    affected_versions: dict = field(default_factory=dict)
    representative_text: str = ""
    representative_conv_id: str = ""
    representative_feedback_id: str = ""


def _affected_versions(convs: List[ConversationRow]) -> dict:
    """精确版本号 → 出现次数。"""
    counter: dict = {}
    for c in convs:
        v = c.appversion or "_unknown"
        counter[v] = counter.get(v, 0) + 1
    return counter


def aggregate_and_score(
    rows: Iterable[ConversationRow],
    *, now_ms: int, weights: dict,
) -> List[CandidateGroup]:
    """对一批候选会话按签名聚合并打分，返回降序排列的 CandidateGroup 列表。"""
    buckets: dict = defaultdict(list)
    for r in rows:
        sig = compute_signature(
            full_text=r.full_text, L2=r.L2, appversion=r.appversion,
        )
        if sig is None:
            continue
        buckets[sig].append(r)

    groups: List[CandidateGroup] = []
    for sig, convs in buckets.items():
        dup_count = len(convs)
        p0_count = sum(1 for c in convs if c.severity == "P0")
        versions = {c.appversion for c in convs if c.appversion}
        cross_version = 1 if len(versions) > 1 else 0
        recent_24h = 1 if any(
            c.last_ts_ms >= now_ms - ONE_DAY_MS for c in convs
        ) else 0

        score = (
            dup_count * weights["w_dup_count"]
            + p0_count * weights["w_p0_count"]
            + cross_version * weights["w_cross_version"]
            + recent_24h * weights["w_recent_24h"]
        )

        rep = max(convs, key=lambda c: c.confidence)
        rep_text = rep.top_conf_text or (rep.full_text or "")[:100]

        groups.append(CandidateGroup(
            signature=sig, conversations=convs, score=score,
            dup_count=dup_count, p0_count=p0_count,
            cross_version=cross_version, recent_24h=recent_24h,
            affected_versions=_affected_versions(convs),
            representative_text=rep_text,
            representative_conv_id=rep.conversation_id,
            representative_feedback_id=rep.top_conf_feedback_id,
        ))

    groups.sort(key=lambda g: g.score, reverse=True)
    return groups


def pick_top5(groups: List[CandidateGroup]) -> List[CandidateGroup]:
    """取前 5；不足 5 个就有几个返几个。"""
    return groups[:5]
