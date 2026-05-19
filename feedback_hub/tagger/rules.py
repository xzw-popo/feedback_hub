"""一审规则引擎（spec §5.2 第 2 步：规则一审 + LLM 兜底）。

设计：
- 规则数据全部在 `rules.yaml`（关键词 / 正则 / 阈值 / 置信度）
- 启动期一次性加载 yaml 并预编译正则；写错立即抛错
- `apply_rules(text)` 命中返回完整标签 dict（含 source='rule'），未命中返回 None
- 判定顺序固定：E.无效 → D.情绪(短) → A.Bug(P0) → B.建议 → C.咨询 → A.Bug(普通)

参考旧 `数据采集与打标/tagger/rules.py` 的判定顺序与置信度策略，**不复用代码**。
"""
from __future__ import annotations

import re
from typing import Optional

import yaml

from feedback_hub.config import PKG_DIR

_RULES_FILE = PKG_DIR / "tagger" / "rules.yaml"


def _load_rules() -> dict:
    if not _RULES_FILE.exists():
        raise FileNotFoundError(f"找不到规则文件：{_RULES_FILE}")
    data = yaml.safe_load(_RULES_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{_RULES_FILE} 顶层必须是 mapping，实际：{type(data).__name__}")

    required = [
        "l2_keywords", "p0_patterns", "bug_patterns", "suggest_patterns",
        "question_patterns", "emotion_pos_patterns", "emotion_neg_patterns",
        "invalid_patterns", "bug_uplift_pattern", "thresholds", "confidence",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"{_RULES_FILE} 缺少字段：{missing}")

    pattern_groups = [
        "p0_patterns", "bug_patterns", "suggest_patterns", "question_patterns",
        "emotion_pos_patterns", "emotion_neg_patterns", "invalid_patterns",
    ]
    for g in pattern_groups:
        for p in data[g]:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"{_RULES_FILE} 正则编译失败 [{g}]: {p!r} -> {e}") from e
    try:
        re.compile(data["bug_uplift_pattern"])
    except re.error as e:
        raise ValueError(
            f"{_RULES_FILE} 正则编译失败 [bug_uplift_pattern]: "
            f"{data['bug_uplift_pattern']!r} -> {e}"
        ) from e
    return data


_R = _load_rules()
_L2_KEYWORDS: dict[str, list[str]] = _R["l2_keywords"]
_P0_PATTERNS: list[str] = _R["p0_patterns"]
_BUG_PATTERNS: list[str] = _R["bug_patterns"]
_SUGGEST_PATTERNS: list[str] = _R["suggest_patterns"]
_QUESTION_PATTERNS: list[str] = _R["question_patterns"]
_EMOTION_POS_PATTERNS: list[str] = _R["emotion_pos_patterns"]
_EMOTION_NEG_PATTERNS: list[str] = _R["emotion_neg_patterns"]
_INVALID_PATTERNS: list[str] = _R["invalid_patterns"]
_BUG_UPLIFT_RE = re.compile(_R["bug_uplift_pattern"])
_TH = _R["thresholds"]
_CONF = _R["confidence"]


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\u2600-\u27BF]+"
)
_PUNCT_RE = re.compile(
    r"[\s。，、；：！？!?,.;:~`'\"…・·\-—_=+*/\\(){}\[\]<>|@#$%^&]+"
)


def _is_pure_emoji_or_punct(text: str) -> bool:
    s = text.strip()
    if not s:
        return True
    s = _EMOJI_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)
    return len(s) == 0


def _detect_l2(text: str) -> list[str]:
    hits: list[str] = []
    for tag, kws in _L2_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                hits.append(tag)
                break
    return hits


def _match_any(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return p
    return None


def _make(l1: str, l2: list[str], severity: str,
          confidence: float, reason: str, rule_name: str) -> dict:
    return {
        "L1": l1,
        "L2": l2,
        "severity": severity,
        "confidence": float(confidence),
        "reason": reason,
        "source": "rule",
        "rule_name": rule_name,
    }


def apply_rules(text: Optional[str]) -> Optional[dict]:
    """命中返回完整标签字典；未命中返回 None。"""
    if text is None:
        return None
    s = text.strip()

    # E.无效
    if not s:
        return _make("E.无效", ["其他"], "P3", _CONF["empty"], "空文本", "rule:empty")
    if len(s) <= _TH["too_short_len"]:
        return _make("E.无效", ["其他"], "P3", _CONF["too_short"],
                     "单字符无效内容", "rule:too_short")
    if _is_pure_emoji_or_punct(s):
        return _make("E.无效", ["符号表情"], "P3", _CONF["pure_emoji"],
                     "纯表情或标点", "rule:pure_emoji")
    hit = _match_any(_INVALID_PATTERNS, s)
    if hit:
        return _make("E.无效", ["其他"], "P3", _CONF["invalid_kw"],
                     f"命中无效模式 {hit}", "rule:invalid_kw")

    # D.情绪（仅短句）
    if len(s) <= _TH["emotion_max_len"]:
        if _match_any(_EMOTION_POS_PATTERNS, s):
            return _make("D.情绪", [], "P3", _CONF["emotion_pos"],
                         "短句正向情绪", "rule:emotion_pos")
        if _match_any(_EMOTION_NEG_PATTERNS, s):
            return _make("D.情绪", [], "P3", _CONF["emotion_neg"],
                         "短句负向情绪", "rule:emotion_neg")

    # A.Bug —— P0
    hit = _match_any(_P0_PATTERNS, s)
    if hit:
        l2 = _detect_l2(s) or ["其他"]
        return _make("A.Bug", l2, "P0", _CONF["bug_p0"],
                     f"P0 关键词 {hit}", "rule:bug_p0")

    # B.建议
    hit = _match_any(_SUGGEST_PATTERNS, s)
    if hit:
        l2 = _detect_l2(s) or ["其他"]
        return _make("B.建议", l2, "P3", _CONF["suggest"],
                     f"建议关键词 {hit}", "rule:suggest")

    # C.咨询
    hit = _match_any(_QUESTION_PATTERNS, s)
    if hit and ("?" in s or "？" in s or len(s) <= _TH["question_short_len"]):
        l2 = _detect_l2(s) or ["其他"]
        return _make("C.咨询", l2, "P3", _CONF["question"],
                     f"咨询句式 {hit}", "rule:question")

    # A.Bug —— 普通级别（P1/P2 由 bug_uplift_pattern 决定）
    hit = _match_any(_BUG_PATTERNS, s)
    if hit:
        l2 = _detect_l2(s) or ["其他"]
        sev = "P1" if _BUG_UPLIFT_RE.search(s) else "P2"
        return _make("A.Bug", l2, sev, _CONF["bug_general"],
                     f"Bug 关键词 {hit}", "rule:bug_general")

    # 未命中 → 交给 LLM
    return None
