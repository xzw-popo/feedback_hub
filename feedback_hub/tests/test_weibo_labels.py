from feedback_hub.weibo.labels import classify_post


def test_classifies_wechat_and_doubao_comparison():
    label = classify_post("微信输入法和豆包输入法比起来，豆包 AI 候选更智能")

    assert label["brand_focus"] == "comparison"
    assert label["sentiment"] == "positive"
    assert "feature_comparison" in label["topics"]
    assert label["risk_level"] == "normal"


def test_classifies_negative_wechat_risk():
    label = classify_post("微信键盘最近广告太烦了，还担心隐私被偷听")

    assert label["brand_focus"] == "wechat"
    assert label["sentiment"] == "negative"
    assert "ads" in label["topics"]
    assert "privacy" in label["topics"]
    assert label["risk_level"] == "watch"


def test_classifies_doubao_ai_topic():
    label = classify_post("豆包输入法的 AI 改写挺好用，推荐试试")

    assert label["brand_focus"] == "doubao"
    assert label["sentiment"] == "positive"
    assert "ai_capability" in label["topics"]
    assert label["post_type"] == "recommendation"


def test_doubao_phone_with_generic_wechat_mention_is_not_comparison():
    label = classify_post("豆包AI手机能帮老人操作软件，不用教爸妈微信怎么用了")

    assert label["brand_focus"] == "doubao"
    assert label["sentiment"] == "neutral"


def test_nearby_wechat_and_doubao_input_method_terms_are_comparison():
    label = classify_post("现在微信和豆包最新的输入法版本都解决了语音转文字权限问题")

    assert label["brand_focus"] == "comparison"
