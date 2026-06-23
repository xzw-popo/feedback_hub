from __future__ import annotations

import re
from typing import Any


WECHAT_TERMS = ("微信输入法", "微信键盘")
DOUBAO_TERMS = ("豆包输入法", "豆包")
COMPARISON_TERMS = ("比", "相比", "不如", "换成", "替代", "对比", "吊打", "更")
POSITIVE_TERMS = ("好用", "流畅", "推荐", "准确", "方便", "智能", "不错", "喜欢")
NEGATIVE_TERMS = ("难用", "崩", "垃圾", "烦", "广告", "隐私", "偷听", "卡", "bug", "问题")
HIGH_RISK_TERMS = ("大规模", "热搜", "泄露", "严重", "投诉")
WEIBO_PRODUCT_TERMS = ("微信输入法", "微信键盘", "豆包输入法")
DOUBAO_CONTEXT_TERMS = ("输入法", "键盘", "语音输入", "AI手机", "ai手机", "手机", "改写")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _has_wechat_input_context(text: str) -> bool:
    if _contains_any(text, WECHAT_TERMS):
        return True
    return bool(re.search(r"微信.{0,12}(输入法|键盘|语音输入|语音转文字)", text))


def _has_doubao_input_context(text: str) -> bool:
    return _contains_any(text, DOUBAO_TERMS) and _contains_any(text, DOUBAO_CONTEXT_TERMS)


def is_relevant_post(text: str, keyword: str = "") -> bool:
    """Return whether a Weibo search result is relevant to the opinion module.

    Weibo PC search treats multi-word queries loosely, so terms like
    "微信键盘 豆包" can return posts that contain none of the product intent.
    The MVP keeps exact product mentions and close brand+input contexts.
    """
    normalized = (text or "").strip()
    if _contains_any(normalized, WEIBO_PRODUCT_TERMS):
        return True
    if _has_wechat_input_context(normalized):
        return True
    if _has_doubao_input_context(normalized):
        return True
    # Keep comparison query results only when visible text contains exact WeChat
    # input-method terms or a Doubao input/phone context.
    return False


def _brand_focus(text: str) -> str:
    has_wechat = _has_wechat_input_context(text)
    has_doubao = _has_doubao_input_context(text) or _contains_any(text, ("豆包输入法",))
    if has_wechat and has_doubao:
        return "comparison"
    if has_doubao:
        return "doubao"
    if has_wechat:
        return "wechat"
    return "other"


def _sentiment(text: str) -> str:
    has_positive = _contains_any(text, POSITIVE_TERMS)
    has_negative = _contains_any(text, NEGATIVE_TERMS)
    if has_positive and has_negative:
        return "mixed"
    if has_positive:
        return "positive"
    if has_negative:
        return "negative"
    return "neutral"


def _topics(text: str, brand_focus: str) -> list[str]:
    topics: list[str] = []
    if _contains_any(text, ("输入", "键盘", "候选", "打字", "拼音", "联想")):
        topics.append("input_experience")
    if _contains_any(text, ("AI", "ai", "智能", "改写", "大模型", "候选")):
        topics.append("ai_capability")
    if _contains_any(text, ("隐私", "偷听", "权限", "数据")):
        topics.append("privacy")
    if _contains_any(text, ("广告", "弹窗", "推广")):
        topics.append("ads")
    if _contains_any(text, ("换成", "卸载", "迁移", "替代")):
        topics.append("migration_intent")
    if brand_focus == "comparison" or _contains_any(text, COMPARISON_TERMS):
        topics.append("feature_comparison")
    if _contains_any(text, ("品牌", "口碑", "认知")):
        topics.append("brand_perception")
    if _contains_any(text, ("崩", "卡", "bug", "闪退", "故障")):
        topics.append("bug")
    return topics or ["other"]


def _post_type(text: str, sentiment: str) -> str:
    if _contains_any(text, ("求助", "怎么", "如何", "有人知道")):
        return "help"
    if _contains_any(text, ("推荐", "安利", "试试")):
        return "recommendation"
    if _contains_any(text, ("评测", "体验", "测评")):
        return "review"
    if _contains_any(text, ("转发", "扩散")):
        return "reshare"
    if _contains_any(text, ("新闻", "发布", "上线")):
        return "news_discussion"
    if sentiment == "negative":
        return "complaint"
    return "other"


def _risk_level(text: str, sentiment: str, topics: list[str]) -> str:
    if _contains_any(text, HIGH_RISK_TERMS):
        return "high"
    if sentiment == "negative" and ("privacy" in topics or "ads" in topics or "bug" in topics):
        return "watch"
    return "normal"


def classify_post(text: str) -> dict[str, Any]:
    """Classify a Weibo post with deterministic MVP rules."""
    normalized = (text or "").strip()
    brand_focus = _brand_focus(normalized)
    sentiment = _sentiment(normalized)
    topics = _topics(normalized, brand_focus)
    post_type = _post_type(normalized, sentiment)
    risk_level = _risk_level(normalized, sentiment, topics)
    return {
        "brand_focus": brand_focus,
        "sentiment": sentiment,
        "topics": topics,
        "post_type": post_type,
        "risk_level": risk_level,
        "confidence": 0.7,
        "reason": "规则命中：品牌、情绪、话题和风险关键词",
        "label_source": "rule",
    }
