## AI 搜索"网络错误"诊断报告

### 问题现象
前端 AI 搜索时报"网络错误"。

### 诊断结果

| 测试项 | 结果 | 说明 |
|---|---|---|
| LLM 直连调用 | ✅ 6.5~33s 成功 | DeepSeek v4-flash 可用，但耗时波动大 |
| GET /api/conversations | ✅ 0.005s | 后端 GET 正常 |
| POST /api/keyword-search | ❌ 超时 | 不调 LLM，但也超时！ |
| POST /api/smart-search | ❌ 超时 | curl 等待 120s 无响应 |
| POST /api/import | ❌ 超时 | 所有 POST 都挂 |
| REGEXP 单条查询 | ✅ 0.04~0.18s | 数据库查询不是瓶颈 |

### 根因

**后端服务所有 POST 请求全部挂死。**

证据：
1. GET 正常但所有 POST 超时 → 不是 LLM 的问题
2. 不涉及 LLM 的 keyword-search POST 也超时 → 后端 POST 处理本身有问题
3. 端口 8000 上有两个 Python 进程（PID 55803 绑 127.0.0.1, PID 60872 绑 0.0.0.0） → 可能冲突
4. 之前测试发送了 smart-search 请求（LLM 调用 25~35s），可能导致工作线程卡住

### 解决方案

**1. 重启后端服务（最优先）**

杀掉现有进程后重新启动：
```bash
kill 55803 60872
cd /Users/charvel/Desktop/用户反馈_2026_0612
python -m feedback_hub.cli serve
```

**2. 前端超时设置（中长期）**

当前 axios 超时 30s，但 LLM 调用可能需要 30+s：
- 方案 A：把前端超时调大到 60s（`http.ts` 中 `timeout: 30000` → `60000`）
- 方案 B：smart-search 改为 SSE 流式（先返回"正在分析"，再推送结果）
- 方案 C：换用更快的模型（如 deepseek-chat 非 thinking 模式）

**3. LLM 耗时波动问题**

deepseek-v4-flash thinking 模式耗时 6~35s 不等，原因：
- thinking 模式消耗大量 token 用于推理
- max_tokens=8192 给了思考很大空间

优化方向：
- 粗筛用非 thinking 模型（如 deepseek-chat），只保留精筛用 thinking
- 降低 max_tokens（但需 >= 4096 否则 content 为空）
- 考虑设 `temperature=0` 减少随机性加速

### 数据库查询性能

REGEXP 查询在 75K 行上单条 0.04~0.18s，不是瓶颈。
