"""快速测试 search LLM 客户端是否能正常调用 DeepSeek v4-flash。"""

from feedback_hub.search.llm_client import chat_completion, generate_regex_patterns, score_relevance

print("=" * 60)
print("测试 1: chat_completion 基础调用")
print("=" * 60)
try:
    reply = chat_completion(
        [{"role": "user", "content": "你好，请回复'测试成功'四个字"}],
        max_tokens=8192,
        timeout=30,
    )
    print(f"✅ chat_completion 成功")
    print(f"   返回内容: {reply[:200]}")
except Exception as e:
    print(f"❌ chat_completion 失败: {e}")

print()
print("=" * 60)
print("测试 2: generate_regex_patterns 粗筛")
print("=" * 60)
try:
    group_patterns, group_logics, group_logic = generate_regex_patterns("语音输入不好用")
    print(f"✅ generate_regex_patterns 成功")
    print(f"   group_patterns: {group_patterns}")
    print(f"   group_logics: {group_logics}")
    print(f"   group_logic: {group_logic}")
except Exception as e:
    print(f"❌ generate_regex_patterns 失败: {e}")

print()
print("=" * 60)
print("测试 3: score_relevance 精筛")
print("=" * 60)
try:
    results = score_relevance(
        "语音输入不好用",
        [{"id": "test-001", "text": "语音输入经常识别错误，说话半天不出字"}],
        timeout=30,
    )
    print(f"✅ score_relevance 成功")
    print(f"   结果: {results}")
except Exception as e:
    print(f"❌ score_relevance 失败: {e}")

print()
print("全部测试完毕。")
