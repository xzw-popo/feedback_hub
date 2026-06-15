"""分步计时测试：精准定位 smart-search 哪一步最慢。"""

import json
import re
import time
import sys

# 强制使用项目内的 feedback_hub
sys.path.insert(0, "/Users/charvel/Desktop/用户反馈_2026_0612")

from feedback_hub import config, db
from feedback_hub.search.llm_client import generate_regex_patterns, score_relevance

print("=" * 60)
print("配置确认")
print("=" * 60)
print(f"  LLM_MODEL: {config.LLM_MODEL}")
print(f"  LLM_API_URL: {config.LLM_API_URL[:50]}...")
print(f"  DB_MODE: {db._db_mode()}")
print(f"  DB_PATH: {config.DB_PATH}")

# --- Step 1: LLM 粗筛 ---
print()
print("=" * 60)
print("Step 1: LLM 粗筛（自然语言 → 正则）")
print("=" * 60)
start = time.time()
try:
    group_patterns, group_logics, group_logic = generate_regex_patterns("语音输入不好用")
    elapsed = time.time() - start
    print(f"✅ 耗时: {elapsed:.1f}s")
    print(f"   group_patterns: {group_patterns}")
    print(f"   group_logics: {group_logics}")
    print(f"   group_logic: {group_logic}")
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ 失败 ({elapsed:.1f}s): {e}")
    sys.exit(1)

# --- Step 2: SQL 构建 ---
print()
print("=" * 60)
print("Step 2: SQL WHERE 子句构建")
print("=" * 60)
start = time.time()

# 模拟 search/api.py 中 _build_regex_where 的逻辑
from feedback_hub.search.api import _build_regex_where, _build_metadata_where, _execute_search_query
from feedback_hub.search.api import MetadataFilters

search_where, search_params = _build_regex_where(group_patterns, group_logics, group_logic)
elapsed = time.time() - start
print(f"✅ 耗时: {elapsed:.3f}s")
print(f"   WHERE: {search_where[:200]}...")
print(f"   params 数量: {len(search_params)}")
if search_params:
    print(f"   params 前5: {search_params[:5]}")

# --- Step 3: 数据库查询 ---
print()
print("=" * 60)
print("Step 3: 数据库查询（REGEXP 匹配）")
print("=" * 60)
filters = MetadataFilters()
start = time.time()
try:
    total, items = _execute_search_query(search_where, search_params, filters, limit=5, offset=0)
    elapsed = time.time() - start
    print(f"✅ 耗时: {elapsed:.2f}s")
    print(f"   total: {total}")
    print(f"   items 数: {len(items)}")
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ 失败 ({elapsed:.1f}s): {e}")

# --- Step 4: 对比 LIKE 查询性能 ---
print()
print("=" * 60)
print("Step 4: 对比 — 同意图用 LIKE 查询（不走 REGEXP）")
print("=" * 60)
like_where = "cl.conversation_id IN (SELECT DISTINCT conversation_id FROM feedback WHERE text LIKE ? OR text LIKE ?)"
like_params = ["%语音输入%", "%语音识别%"]
start = time.time()
try:
    total2, items2 = _execute_search_query(like_where, like_params, filters, limit=5, offset=0)
    elapsed = time.time() - start
    print(f"✅ 耗时: {elapsed:.2f}s")
    print(f"   total: {total2}")
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ 失败 ({elapsed:.1f}s): {e}")

# --- Step 5: 数据库总行数 ---
print()
print("=" * 60)
print("Step 5: 数据库规模")
print("=" * 60)
with db.connect() as conn:
    fb_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()
    cl_count = conn.execute("SELECT COUNT(*) FROM conversation_label").fetchone()
    print(f"   feedback 行数: {fb_count[0]}")
    print(f"   conversation_label 行数: {cl_count[0]}")

# --- Step 6: 单独测 REGEXP ---
print()
print("=" * 60)
print("Step 6: 单独测 REGEXP 查询性能")
print("=" * 60)
test_patterns = group_patterns[0] if group_patterns else ["语音输入"]
for p in test_patterns[:3]:
    start = time.time()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE text REGEXP ?",
            (p,),
        ).fetchone()
    elapsed = time.time() - start
    print(f"   REGEXP '{p[:50]}': {elapsed:.2f}s, 匹配 {rows[0]} 行")

# --- 总结 ---
print()
print("=" * 60)
print("瓶颈定位总结")
print("=" * 60)
print("对比各步骤耗时，最慢的即为瓶颈。")
