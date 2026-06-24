"""OpenAI 兼容 LLM 客户端，用于智能搜索的正则生成和精筛评分。

与 tagger/llm_client.py（Wink Agent 专有协议）并行存在，各自独立。
本模块使用标准 OpenAI chat/completions 协议。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import requests

from feedback_hub import config


class SearchLLMError(RuntimeError):
    """搜索 LLM 客户端错误。"""


def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 16384,
    timeout: int = 60,
) -> str:
    """调用 OpenAI 兼容 API，返回 assistant 回复文本。

    兼容 DeepSeek v4 等支持思考模式的模型：
    - max_tokens 设为 16384，给思考和回复留够空间（思考模式会消耗大量 token）
    - 优先取 content，若为空则报错（reasoning_content 是思考过程，不是我们要的输出）
    """
    if not config.LLM_API_KEY.strip():
        raise SearchLLMError("LLM_API_KEY is not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LLM_API_KEY}",
    }
    payload: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(
            config.LLM_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content", "") or ""
        if not content.strip():
            raise SearchLLMError(
                "LLM returned empty content (model may have used all tokens for reasoning)"
            )
        return content
    except (KeyError, IndexError) as e:
        raise SearchLLMError(f"Unexpected LLM response format: {e}") from e
    except requests.RequestException as e:
        raise SearchLLMError(f"LLM API request failed: {e}") from e


# ---------------------------------------------------------------------------
# 粗筛：自然语言 → 正则表达式
# ---------------------------------------------------------------------------

_INTENT_SYSTEM_PROMPT = """你是一个搜索意图解析器。根据用户想查找的反馈，提取可安全执行的关键词条件。

返回 JSON（不要其他内容）：
{"must": [["必须词或同义词1", "同义词2"]], "should": [["可选词或同义词"]], "exclude": ["排除词"]}

规则：
- must 是必须满足的条件组；组内任一词命中即可，组与组之间是 AND
- should 是增强召回的条件组；仅当没有 must 时使用，组之间是 OR
- exclude 是需要排除的词
- 只返回普通词组，不要返回正则、SQL、解释文字
- 每个词组不超过 30 个字符"""

_REGEX_SYSTEM_PROMPT = """你是一个搜索条件生成器。根据用户的搜索意图生成正则表达式，覆盖各种同义/近义/口语化表述。

返回 JSON（不要其他内容）：
{"groups": [{"patterns": ["正则1", "正则2"], "logic": "AND或OR"}, ...], "group_logic": "AND或OR"}

规则：
- groups 是条件组数组，每组内 patterns 按 logic(AND/OR) 组合
- group_logic 是组之间的关系（通常为 OR，表示满足任一组即可）
- 每个正则 ≤ 200 字符
- 禁止回溯型模式（如 (.*)* ）
- 不同维度的条件应拆为不同组（如"语音输入不好用"→组1覆盖语音+负面，组2可覆盖键盘+卡顿等同义场景）"""

# 已知的回溯型正则模式（用于安全校验）
_BACKTRACKING_RE = re.compile(r"(\.\*[*+]|\.\+[*+]|\([\s\S]*\)[*+])")

# 裸贪婪量词 .* / .+ 计数：单条文本里出现多个会显著放大回溯风险
# （如 A.*B.*C.*D 在长文本上接近指数级）。配合长度截断双保险，这里直接拒绝。
_GREEDY_WILDCARD_RE = re.compile(r"\.[*+]")
_MAX_GREEDY_WILDCARDS: int = 1


@lru_cache(maxsize=512)
def generate_search_intent(query: str) -> dict[str, list]:
    """调用 LLM 将自然语言查询翻译为受控关键词意图。"""
    messages = [
        {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    reply = chat_completion(messages, temperature=0, max_tokens=2048)
    return _parse_search_intent_reply(reply)


def _parse_search_intent_reply(reply: str) -> dict[str, list]:
    """解析 LLM 返回的关键词意图 JSON。"""
    json_match = re.search(r"```(?:json)?\s*(.*?)```", reply, re.DOTALL)
    text = json_match.group(1).strip() if json_match else reply.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SearchLLMError(f"LLM returned invalid intent JSON: {reply[:200]}") from e

    if not isinstance(data, dict):
        raise SearchLLMError("LLM returned invalid intent format")

    intent = {
        "must": _normalize_term_groups(data.get("must", [])),
        "should": _normalize_term_groups(data.get("should", [])),
        "exclude": _normalize_terms(data.get("exclude", [])),
    }
    if not intent["must"] and not intent["should"] and not intent["exclude"]:
        raise SearchLLMError("LLM generated no valid intent terms")
    return intent


def _normalize_term_groups(groups: Any) -> list[list[str]]:
    if not isinstance(groups, list):
        return []
    normalized: list[list[str]] = []
    for group in groups:
        terms = _normalize_terms(group)
        if terms:
            normalized.append(terms)
    return normalized


def _normalize_terms(terms: Any) -> list[str]:
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, list):
        return []

    normalized: list[str] = []
    for term in terms:
        if not isinstance(term, str):
            continue
        term = re.sub(r"\s+", " ", term).strip()
        if not term or len(term) > 30:
            continue
        if term not in normalized:
            normalized.append(term)
    return normalized


def generate_regex_patterns(query: str) -> tuple[list[list[str]], list[str], str]:
    """调用 LLM 将自然语言查询翻译为分组正则表达式。

    Returns:
        (group_patterns, group_logics, group_logic)
        - group_patterns: 每组的正则列表，如 [["语音输入|语音转文字", "不好用|用不了"], ["键盘|输入法", "卡顿"]]
        - group_logics: 每组内部的逻辑，如 ["AND", "AND"]
        - group_logic: 组间逻辑，"AND" 或 "OR"
    """
    messages = [
        {"role": "system", "content": _REGEX_SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    reply = chat_completion(messages, temperature=0, max_tokens=8192)
    return _parse_regex_reply(reply)


def _parse_regex_reply(reply: str) -> tuple[list[list[str]], list[str], str]:
    """解析 LLM 返回的分组 JSON。

    多层容错：markdown 代码块 → 纯 JSON → 字段校验 → 正则合法性。
    同时兼容旧格式 {"patterns": [...], "logic": ...}。
    """
    # 从 markdown 代码块中提取
    json_match = re.search(r"```(?:json)?\s*(.*?)```", reply, re.DOTALL)
    text = json_match.group(1).strip() if json_match else reply.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SearchLLMError(f"LLM returned invalid JSON: {reply[:200]}") from e

    # 兼容旧格式：{"patterns": [...], "logic": "..."}
    if "patterns" in data and "groups" not in data:
        old_patterns = data.get("patterns", [])
        old_logic = data.get("logic", "OR")
        valid = _validate_patterns(old_patterns)
        if not valid:
            raise SearchLLMError("LLM generated no valid regex patterns")
        return [valid], [old_logic], "OR"

    groups = data.get("groups", [])
    group_logic = data.get("group_logic", "OR")

    group_patterns = []
    group_logics = []
    for g in groups:
        patterns = g.get("patterns", [])
        logic = g.get("logic", "OR")
        valid = _validate_patterns(patterns)
        if valid:
            group_patterns.append(valid)
            group_logics.append(logic if logic in ("AND", "OR") else "OR")

    if not group_patterns:
        raise SearchLLMError("LLM generated no valid regex patterns")

    return group_patterns, group_logics, group_logic if group_logic in ("AND", "OR") else "OR"


def _validate_patterns(patterns: list) -> list[str]:
    """校验正则列表，返回合法的正则。

    过滤规则：
    - 非字符串 / 超长（>200）
    - 无法编译
    - 嵌套量词回溯模式（_BACKTRACKING_RE）
    - 含 ≥2 个裸贪婪量词 .* / .+（多段贪婪在长文本上接近指数级回溯）
    """
    valid = []
    for p in patterns:
        if not isinstance(p, str):
            continue
        if len(p) > 200:
            continue
        try:
            re.compile(p)
        except re.error:
            continue
        if _BACKTRACKING_RE.search(p):
            continue
        if len(_GREEDY_WILDCARD_RE.findall(p)) > _MAX_GREEDY_WILDCARDS:
            continue
        valid.append(p)
    return valid


# ---------------------------------------------------------------------------
# 精筛：反馈相关性评分
# ---------------------------------------------------------------------------

_SCORE_SYSTEM_PROMPT = """判断反馈与用户意图的相关程度，返回评分。

评分：3=高相关 2=中相关 1=低相关

返回 JSON（不要其他内容）：
{"id": "会话ID", "score": 1-3, "reason": "简短理由"}"""


def score_relevance(
    query: str,
    feedbacks: list[dict[str, Any]],
    *,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """对一批反馈做相关性评分。

    支持单条或多条输入。单条时 prompt 更精简，LLM 响应更快。

    Args:
        query: 用户原始意图
        feedbacks: 待评分的反馈列表，每项需含 "id" 和 "text" 字段

    Returns:
        评分列表，每项 {"id": ..., "score": 1-3, "reason": "..."}
    """
    if len(feedbacks) == 1:
        # 单条模式：更精简的 prompt，减少 thinking 开销
        f = feedbacks[0]
        messages = [
            {"role": "system", "content": _SCORE_SYSTEM_PROMPT},
            {"role": "user", "content": f"用户意图：{query}\n反馈内容：{f['text'][:300]}\n会话ID：{f['id']}"},
        ]
    else:
        # 多条模式：兼容批量
        numbered = "\n".join(
            f"- [id={f['id']}] {f['text'][:200]}"
            for f in feedbacks
        )
        messages = [
            {"role": "system", "content": _SCORE_SYSTEM_PROMPT.replace(
                '返回 JSON（不要其他内容）：\n{"id": "会话ID", "score": 1-3, "reason": "简短理由"}',
                '返回 JSON 数组（不要其他内容）：\n[{"id": "会话ID", "score": 1-3, "reason": "简短理由"}, ...]'
            )},
            {"role": "user", "content": f"## 用户意图\n{query}\n\n## 待判断的反馈\n{numbered}"},
        ]

    reply = chat_completion(messages, temperature=0.1, timeout=timeout)
    return _parse_score_reply(reply, len(feedbacks))


def _parse_score_reply(reply: str, expected_count: int) -> list[dict[str, Any]]:
    """解析 LLM 返回的评分 JSON。兼容单条对象和数组格式。"""
    json_match = re.search(r"```(?:json)?\s*(.*?)```", reply, re.DOTALL)
    text = json_match.group(1).strip() if json_match else reply.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 解析失败，全部返回默认评分 2
        return [{"id": i + 1, "score": 2, "reason": "parse error"} for i in range(expected_count)]

    # 单条对象格式：{"id": "xxx", "score": 3, "reason": "..."}
    if isinstance(data, dict):
        score = data.get("score", 2)
        if not isinstance(score, int) or score not in (1, 2, 3):
            score = 2
        return [{
            "id": data.get("id", 0),
            "score": score,
            "reason": data.get("reason", ""),
        }]

    # 数组格式：[{"id": "xxx", "score": 3, ...}, ...]
    if not isinstance(data, list):
        return [{"id": i + 1, "score": 2, "reason": "format error"} for i in range(expected_count)]

    results = []
    for item in data:
        score = item.get("score", 2)
        if not isinstance(score, int) or score not in (1, 2, 3):
            score = 2
        results.append({
            "id": item.get("id", 0),
            "score": score,
            "reason": item.get("reason", ""),
        })

    return results
