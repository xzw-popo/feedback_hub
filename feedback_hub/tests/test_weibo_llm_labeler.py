import pytest

from feedback_hub.weibo.llm_labeler import WeiboLLMLabelError, classify_post_with_llm, parse_label_reply


def test_parse_label_reply_accepts_fenced_json_and_normalizes_values():
    label = parse_label_reply(
        """
        ```json
        {
          "is_relevant": true,
          "relevance_reason": "讨论豆包输入法和微信输入法体验",
          "brand_focus": "comparison",
          "sentiment": "mixed",
          "topics": ["feature_comparison", "keyboard", "ai_capability"],
          "post_type": "review",
          "risk_level": "watch",
          "summary": "用户比较两款输入法。",
          "reason": "既提到豆包 AI，也提到微信输入法。",
          "confidence": 0.82
        }
        ```
        """
    )

    assert label["is_relevant"] is True
    assert label["brand_focus"] == "comparison"
    assert label["topics"] == ["feature_comparison", "ai_capability"]
    assert label["risk_level"] == "watch"
    assert label["label_source"] == "llm"
    assert label["confidence"] == 0.82


def test_parse_label_reply_rejects_invalid_required_enum():
    with pytest.raises(WeiboLLMLabelError):
        parse_label_reply(
            {
                "is_relevant": True,
                "brand_focus": "keyboard",
                "sentiment": "neutral",
                "topics": ["other"],
                "post_type": "other",
                "risk_level": "normal",
                "reason": "bad enum",
            }
        )


def test_classify_post_with_llm_sends_post_and_keyword_context():
    seen = {}

    def fake_chat(messages, **kwargs):
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return """
        {
          "is_relevant": false,
          "relevance_reason": "只是企业微信和物理键盘段子",
          "brand_focus": "other",
          "sentiment": "neutral",
          "topics": ["other"],
          "post_type": "other",
          "risk_level": "normal",
          "summary": "无关段子",
          "reason": "没有讨论微信输入法、豆包输入法或二者对比。",
          "confidence": 0.9
        }
        """

    label = classify_post_with_llm(
        {
            "weibo_id": "3001",
            "text": "猫踩键盘，在企业微信群里发了半小时乱码",
            "keywords": ["微信键盘 豆包"],
        },
        chat_fn=fake_chat,
    )

    assert label["is_relevant"] is False
    assert label["brand_focus"] == "other"
    assert "微信键盘 豆包" in seen["messages"][1]["content"]
    assert "猫踩键盘" in seen["messages"][1]["content"]
    assert seen["kwargs"]["temperature"] == 0
