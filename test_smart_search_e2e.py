"""端到端测试：模拟前端 AI 搜索请求，定位网络错误根因。

测试内容：
1. 后端能否启动
2. /api/smart-search 端点是否可达
3. LLM 调用是否正常返回
4. 请求耗时是否超过前端 30s 超时
5. SSE 精筛端点是否可达
"""

import json
import time
import sys
import requests

BASE_URL = "http://localhost:8000"

def test_health():
    """测试后端是否运行。"""
    print("=" * 60)
    print("测试 1: 后端服务是否运行")
    print("=" * 60)
    try:
        r = requests.get(f"{BASE_URL}/api/conversations?limit=1", timeout=5)
        print(f"✅ 后端运行中，状态码: {r.status_code}")
        return True
    except requests.ConnectionError:
        print("❌ 后端未运行或端口 8000 不可达")
        print("   请先启动: python -m feedback_hub.cli serve")
        return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_smart_search():
    """测试 AI 搜索端点。"""
    print()
    print("=" * 60)
    print("测试 2: /api/smart-search 端点")
    print("=" * 60)
    payload = {
        "query": "语音输入不好用",
        "filters": {},
        "limit": 5,
        "offset": 0,
    }
    start = time.time()
    try:
        r = requests.post(
            f"{BASE_URL}/api/smart-search",
            json=payload,
            timeout=60,  # 给 LLM 调用留够时间
        )
        elapsed = time.time() - start
        print(f"   状态码: {r.status_code}")
        print(f"   耗时: {elapsed:.1f}s")
        if r.status_code == 200:
            data = r.json()
            print(f"✅ smart-search 成功")
            print(f"   total: {data.get('total', 'N/A')}")
            print(f"   items 数: {len(data.get('items', []))}")
            if data.get("debug"):
                print(f"   debug: {json.dumps(data['debug'], ensure_ascii=False)[:300]}")
            if elapsed > 30:
                print(f"⚠️  耗时 {elapsed:.1f}s 超过前端 axios 30s 超时！这会导致前端报网络错误")
        else:
            print(f"❌ smart-search 返回非 200")
            print(f"   响应: {r.text[:500]}")
    except requests.Timeout:
        elapsed = time.time() - start
        print(f"❌ 请求超时 ({elapsed:.1f}s)")
        print("   可能原因: LLM API 响应太慢")
    except requests.ConnectionError as e:
        print(f"❌ 连接错误: {e}")
    except Exception as e:
        print(f"❌ 未知错误: {e}")


def test_keyword_search():
    """测试关键词搜索端点（不涉及 LLM，作为基线对比）。"""
    print()
    print("=" * 60)
    print("测试 3: /api/keyword-search 端点（基线，不调 LLM）")
    print("=" * 60)
    payload = {
        "groups": [{"keywords": ["语音输入"], "logic": "OR"}],
        "excludes": [],
        "filters": {},
        "limit": 5,
        "offset": 0,
    }
    start = time.time()
    try:
        r = requests.post(
            f"{BASE_URL}/api/keyword-search",
            json=payload,
            timeout=10,
        )
        elapsed = time.time() - start
        print(f"   状态码: {r.status_code}, 耗时: {elapsed:.2f}s")
        if r.status_code == 200:
            data = r.json()
            print(f"✅ keyword-search 成功, total: {data.get('total', 'N/A')}")
        else:
            print(f"❌ keyword-search 失败: {r.text[:300]}")
    except Exception as e:
        print(f"❌ 请求失败: {e}")


def test_fine_filter():
    """测试 SSE 精筛端点。"""
    print()
    print("=" * 60)
    print("测试 4: /api/fine-filter SSE 端点")
    print("=" * 60)
    # 先用关键词搜索拿几个会话 ID
    try:
        r = requests.post(
            f"{BASE_URL}/api/keyword-search",
            json={"groups": [{"keywords": ["语音"], "logic": "OR"}], "excludes": [], "filters": {}, "limit": 3, "offset": 0},
            timeout=10,
        )
        if r.status_code != 200 or not r.json().get("items"):
            print("⚠️  无法获取测试用会话 ID，跳过精筛测试")
            return
        ids = [it["conversation_id"] for it in r.json()["items"]]
        print(f"   测试会话: {ids}")
    except Exception as e:
        print(f"⚠️  无法获取测试会话: {e}")
        return

    payload = {
        "query": "语音输入不好用",
        "conversation_ids": ids,
    }
    start = time.time()
    try:
        r = requests.post(
            f"{BASE_URL}/api/fine-filter",
            json=payload,
            timeout=60,
            stream=True,
        )
        print(f"   状态码: {r.status_code}")
        print(f"   Content-Type: {r.headers.get('Content-Type', 'N/A')}")
        if r.status_code == 200:
            events = []
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                events.append(line)
                if len(events) <= 15:
                    print(f"   SSE: {line[:120]}")
            elapsed = time.time() - start
            print(f"✅ fine-filter SSE 完成, 耗时: {elapsed:.1f}s, 事件数: {len(events)}")
            if elapsed > 30:
                print(f"⚠️  耗时 {elapsed:.1f}s 超过前端 30s 超时！")
        else:
            print(f"❌ fine-filter 返回非 200: {r.text[:500]}")
    except Exception as e:
        print(f"❌ 请求失败: {e}")


def test_llm_direct():
    """直接测试 LLM API 调用（绕过后端）。"""
    print()
    print("=" * 60)
    print("测试 5: 直接调用 DeepSeek API")
    print("=" * 60)
    from feedback_hub.search.llm_client import generate_regex_patterns
    start = time.time()
    try:
        group_patterns, group_logics, group_logic = generate_regex_patterns("语音输入不好用")
        elapsed = time.time() - start
        print(f"✅ LLM 直接调用成功, 耗时: {elapsed:.1f}s")
        print(f"   结果: {group_patterns}")
        if elapsed > 25:
            print(f"⚠️  耗时 {elapsed:.1f}s，加上后端开销后可能超过前端 30s 超时！")
    except Exception as e:
        elapsed = time.time() - start
        print(f"❌ LLM 调用失败 ({elapsed:.1f}s): {e}")


if __name__ == "__main__":
    # 先测 LLM 直连，不需要后端
    test_llm_direct()

    # 再测后端 API
    if test_health():
        test_keyword_search()
        test_smart_search()
        test_fine_filter()
    else:
        print()
        print("后端未启动，跳过 API 测试。可手动启动后重试：")
        print("  python -m feedback_hub.cli serve")

    print()
    print("=" * 60)
    print("诊断建议")
    print("=" * 60)
    print("- 如果 LLM 直连慢但成功 → 问题在耗时超前端 30s 超时")
    print("- 如果 LLM 直连失败 → 问题在 API Key / 模型名 / 网络连通性")
    print("- 如果后端不运行 → 需先启动后端")
    print("- 如果 smart-search 返回 503 → LLM 服务不可用，检查报错信息")
