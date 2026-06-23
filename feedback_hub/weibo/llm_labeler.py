from __future__ import annotations

import json
import re
from typing import Any, Callable

from feedback_hub.search.llm_client import chat_completion
from feedback_hub.weibo.models import (
    BRAND_FOCUS_VALUES,
    POST_TYPE_VALUES,
    RISK_LEVEL_VALUES,
    SENTIMENT_VALUES,
    TOPIC_VALUES,
)


class WeiboLLMLabelError(ValueError):
    pass


SYSTEM_PROMPT = """你是微博用户反馈与舆论分析标注员。

任务：判断一条微博是否与“微信输入法/微信键盘、豆包输入法/豆包相关输入体验、二者对比舆论”有关，并输出结构化 JSON。

严格要求：
1. 关键词只代表召回来源，不能直接当作标签依据。
2. “微信”“键盘”单独出现不等于微信输入法；企业微信、物理键盘、游戏键盘、闲聊段子通常无关。
3. “豆包”可能指 AI 助手、应用、手机 AI 能力或输入体验。只有能体现豆包相关舆论，或与微信输入法/键盘形成比较，才算相关。
4. 如果微博只是营销抽奖、无意义转发、搜索噪声、泛娱乐内容，is_relevant=false。
5. 只能返回 JSON，不要返回解释文字。

枚举：
brand_focus: wechat | doubao | comparison | other
sentiment: positive | neutral | negative | mixed
topics: input_experience | ai_capability | privacy | ads | migration_intent | feature_comparison | brand_perception | bug | other
post_type: complaint | help | recommendation | review | reshare | news_discussion | other
risk_level: normal | watch | high

JSON 字段：
is_relevant(boolean), relevance_reason(string), brand_focus, sentiment, topics(array), post_type, risk_level,
summary(string), reason(string), confidence(number 0-1)
"""


def _extract_json(reply: str) -> Any:
    text = reply.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WeiboLLMLabelError(f"invalid llm json: {exc}") from exc


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise WeiboLLMLabelError("is_relevant must be boolean")


def _enum(value: Any, allowed: tuple[str, ...], field: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise WeiboLLMLabelError(f"{field} must be one of {allowed}")
    return normalized


def _topics(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_topics = [value]
    elif isinstance(value, list):
        raw_topics = value
    else:
        raw_topics = []
    out: list[str] = []
    for item in raw_topics:
        topic = str(item or "").strip()
        if topic in TOPIC_VALUES and topic not in out:
            out.append(topic)
    return out or ["other"]


def _confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise WeiboLLMLabelError("confidence must be a number") from exc
    return max(0.0, min(score, 1.0))


def parse_label_reply(reply: str | dict[str, Any]) -> dict[str, Any]:
    payload = _extract_json(reply) if isinstance(reply, str) else reply
    if not isinstance(payload, dict):
        raise WeiboLLMLabelError("llm label must be an object")
    is_relevant = _coerce_bool(payload.get("is_relevant"))
    return {
        "is_relevant": is_relevant,
        "relevance_reason": str(payload.get("relevance_reason") or "").strip(),
        "brand_focus": _enum(payload.get("brand_focus"), BRAND_FOCUS_VALUES, "brand_focus"),
        "sentiment": _enum(payload.get("sentiment"), SENTIMENT_VALUES, "sentiment"),
        "topics": _topics(payload.get("topics")),
        "post_type": _enum(payload.get("post_type"), POST_TYPE_VALUES, "post_type"),
        "risk_level": _enum(payload.get("risk_level"), RISK_LEVEL_VALUES, "risk_level"),
        "summary": str(payload.get("summary") or "").strip(),
        "reason": str(payload.get("reason") or payload.get("relevance_reason") or "").strip(),
        "confidence": _confidence(payload.get("confidence")),
        "label_source": "llm",
    }


def build_label_messages(post: dict[str, Any]) -> list[dict[str, str]]:
    keywords = post.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    user_payload = {
        "weibo_id": post.get("weibo_id"),
        "keywords": keywords,
        "author_name": post.get("author_name") or "",
        "created_at_raw": post.get("created_at_raw") or "",
        "text": post.get("text") or "",
        "counts": {
            "reposts": post.get("reposts_count"),
            "comments": post.get("comments_count"),
            "attitudes": post.get("attitudes_count"),
        },
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def classify_post_with_llm(
    post: dict[str, Any],
    *,
    chat_fn: Callable[..., str] = chat_completion,
) -> dict[str, Any]:
    reply = chat_fn(
        build_label_messages(post),
        temperature=0,
        max_tokens=1200,
        timeout=30,
    )
    return parse_label_reply(reply)
