"""分步计时测试（v2）：绕过 FastAPI，直接测 LLM + REGEXP 数据库性能。"""

import time
import sys

sys.path.insert(0, "/Users/charvel/Desktop/用户反馈_2026_0612")

from feedback_hub import config, db
from feedback_hub.search.llm_client import generate_regex_patterns

print("=" * 60)
print("配置确认")
print("=" * 60)
print(f"  LLM_MODEL: {config.LLM_MODEL}")
print(f"  LLM_API_URL: {config.LLM_API_URL[:50]}...")
print(f"  DB_MODE: {db._db_mode()}")

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
    for i, (p, l) in enumerate(zip(group_patterns, group_logics)):
        print(f"   组{i}: logic={l}, patterns={p[:3]}...")
    print(f"   group_logic: {group_logic}")
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ 失败 ({elapsed:.1f}s): {e}")
    sys.exit(1)

# --- Step 2: 数据库规模 ---
print()
print("=" * 60)
print("Step 2: 数据库规模")
print("=" * 60)
with db.connect() as conn:
    fb_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    cl_count = conn.execute("SELECT COUNT(*) FROM conversation_label").fetchone()[0]
    print(f"   feedback 行数: {fb_count}")
    print(f"   conversation_label 行数: {cl_count}")

# --- Step 3: 逐条 REGEXP 性能测试 ---
print()
print("=" * 60)
print("Step 3: 逐条 REGEXP 查询性能")
print("=" * 60)
all_patterns = [p for grp in group_patterns for p in grp]
for p in all_patterns[:6]:
    start = time.time()
    with db.connect() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM feedback WHERE text REGEXP ?", (p,)).fetchone()[0]
    elapsed = time.time() - start
    print(f"   REGEXP '{p[:60]}': {elapsed:.2f}s, 匹配 {cnt} 行")

# --- Step 4: 组合 REGEXP 查询（模拟 _build_regex_where） ---
print()
print("=" * 60)
print("Step 4: 组合 REGEXP WHERE 查询（模拟 smart-search 完整 SQL）")
print("=" * 60)

# 构建类似 search/api.py 的 WHERE
parts = []
params = []
for patterns, logic in zip(group_patterns, group_logics):
    inner = []
    for p in patterns:
        inner.append("text REGEXP ?")
        params.append(p)
    joiner = " AND " if logic == "AND" else " OR "
    parts.append(f"({joiner.join(inner)})")

group_joiner = " AND " if group_logic == "AND" else " OR "
inner_where = group_joiner.join(parts)
search_where = f"cl.conversation_id IN (SELECT DISTINCT conversation_id FROM feedback WHERE {inner_where})"

print(f"   WHERE 子句: {search_where[:200]}...")
print(f"   params 数量: {len(params)}")

start = time.time()
try:
    with db.connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM conversation_label cl {search_where}",
            tuple(params),
        ).fetchone()[0]
    elapsed = time.time() - start
    print(f"✅ 耗时: {elapsed:.2f}s, total: {total}")
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ 失败 ({elapsed:.1f}s): {e}")

# --- Step 5: 对比 LIKE 查询 ---
print()
print("=" * 60)
print("Step 5: 对比 — LIKE 查询（不走 REGEXP）")
print("=" * 60)
like_where = "cl.conversation_id IN (SELECT DISTINCT conversation_id FROM feedback WHERE text LIKE ? OR text LIKE ?)"
like_params = ("%语音输入%", "%语音识别%")
start = time.time()
try:
    with db.connect() as conn:
        total2 = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM conversation_label cl {like_where}",
            like_params,
        ).fetchone()[0]
    elapsed = time.time() - start
    print(f"✅ 耗时: {elapsed:.2f}s, total: {total2}")
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ 失败 ({elapsed:.1f}s): {e}")

# --- Step 6: 总耗时预估 ---
print()
print("=" * 60)
print("瓶颈总结")
print("=" * 60)
print("前端 axios 超时: 30s")
print("LLM 粗筛一般耗时: 20~35s（deepseek-v4-flash thinking 模式）")
print("REGEXP 查询耗时: 取决于正则复杂度和数据量")
print("总耗时 = LLM + SQL，如果 > 30s → 前端报网络错误")
