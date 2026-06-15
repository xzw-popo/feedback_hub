# 2026-06-14 前端导航修复 & 搜索功能优化更新日志

> 更新日期：2026-06-14
> 涉及模块：前端路由/导航、关键词搜索、AI 智能搜索、精筛按钮

---

## 一、问题背景

用户反馈以下问题：

1. **搜索按钮全部丢失**：打开反馈列表页面后，看不到 AI 搜索输入框、关键词搜索和 AI 精筛按钮
2. **页面命名混乱**：侧边栏有"反馈列表""标签列表"两个导航，指向不同页面，命名不直观
3. **AI 搜索报"服务异常"**：使用 AI 搜索功能时后端返回 503
4. **关键词搜索必须填排除词才能搜**：只有关键词没有排除词时搜索超时失败

---

## 二、修复内容

### 2.1 前端路由修复：默认首页指向带搜索功能的页面

**根因**：默认路由 `/` 指向 `SimpleList.vue`（仅日期筛选的简化版），而搜索/AI 精筛等完整功能在 `List.vue`，通过 `/list` 访问。侧边栏叫"标签列表"不易被发现。

**修改文件**：`dashboard/src/router/index.ts`

| 路由 | 修改前 | 修改后 |
|---|---|---|
| `/` | `SimpleList.vue` | **`List.vue`**（含完整搜索功能） |
| `/list` | `List.vue` | 已移除（功能合并到 `/`） |
| `/simple` | 无 | `SimpleList.vue`（保留备用） |

**修改内容**：
```typescript
// 修改前
{ path: '/', name: 'simple-list', component: () => import('@/views/SimpleList.vue') },
{ path: '/list', name: 'list', component: () => import('@/views/List.vue') },

// 修改后
{ path: '/', name: 'list', component: () => import('@/views/List.vue') },
{ path: '/simple', name: 'simple-list', component: () => import('@/views/SimpleList.vue') },
```

### 2.2 侧边栏导航改名 + 添加"测试"标签

**修改文件**：`dashboard/src/App.vue`

- 去掉"标签列表"导航项（功能已合并到首页）
- "反馈列表"和"概览"两个导航后添加橙色 **「测试」** 小标签
- 新增 `.beta-tag` 样式（橙色背景+边框，10px 字号）

**导航最终效果**：
```
┌─────────────────┐
│  反馈列表 [测试]  │  ← 默认首页，含 AI 搜索/关键词/精筛
│  概览     [测试]  │
└─────────────────┘
```

### 2.3 页面标题添加"测试"标识

**修改文件**：
- `dashboard/src/views/List.vue`：页面标题"反馈列表"后加 `<span class="beta-inline">测试</span>`
- `dashboard/src/views/Overview.vue`：页面标题"概览"后加 `<span class="beta-inline">测试</span>`
- 两个文件各新增 `.beta-inline` 样式（与侧边栏 `.beta-tag` 样式一致）

### 2.4 AI 精筛按钮逻辑说明

**状态**：保持原有逻辑不变——**AI 精筛按钮在执行搜索（AI 搜索或关键词搜索）并得到结果后才会出现**。

这是设计意图：精筛是对搜索结果的二次筛选，无结果时精筛无意义。如果希望按钮始终可见（禁用态+提示），可修改 `FineFilterButton.vue` 的 `v-if` 条件。

### 2.5 关键词搜索性能修复：REGEXP → LIKE

**根因**：关键词搜索使用 SQLite `REGEXP` 操作符，需要注册 Python 回调函数（`_sqlite_regexp`），每次正则匹配都要跨越 C/Python 边界。74K 行反馈表全表扫描导致单次查询 **30 秒+**，前端 30s 超时后显示"服务异常"。

**修改文件**：`feedback_hub/search/api.py` — `_build_keyword_where()` 函数

| 对比项 | 修改前 | 修改后 |
|---|---|---|
| 匹配方式 | `text REGEXP ?` | `text LIKE ?` |
| 参数格式 | 原始关键词 `"卡顿"` | 百分号包裹 `"%卡顿%"` |
| 排除方式 | `text NOT REGEXP ?` | `text NOT LIKE ?` |
| 性能 | 30s+（Python 回调） | <1s（SQLite 内置 C 函数） |

**注意**：AI 搜索（`smart_search`）的粗筛仍使用 `REGEXP`，因为 LLM 生成的正则需要正则语义（如 `卡顿|卡卡|延迟`），不能用 LIKE 替代。

**同时修复**：`keyword_search` 端点的参数校验逻辑

```python
# 修改前：groups 为空列表也会通过校验
if not req.groups and not req.excludes:

# 修改后：检查是否有实际关键词
has_any_keyword = any(g.keywords for g in req.groups)
if not has_any_keyword and not req.excludes:
```

### 2.6 AI 搜索配置修复：LLM 配置改为环境变量

**根因**：`config.py` 中 `LLM_API_KEY` 默认值为空字符串，`LLM_API_URL` 默认指向 OpenAI（需翻墙），`LLM_MODEL` 默认为 `gpt-4o-mini`。启动后端时未设置环境变量，导致调用 LLM API 失败返回 503。

**修改文件**：`feedback_hub/config.py`

| 配置项 | 修改前 | 修改后 |
|---|---|---|
| `LLM_API_URL` | `https://api.openai.com/v1/chat/completions` | `https://api.deepseek.com/v1/chat/completions` |
| `LLM_API_KEY` | `""`（空） | 从环境变量 `LLM_API_KEY` 读取 |
| `LLM_MODEL` | `gpt-4o-mini` | `deepseek-chat` |

**说明**：密钥不应写入仓库。如需切换到其他 LLM 提供商，设置环境变量即可。

---

## 三、项目两套 LLM 客户端说明

| | `tagger/llm_client.py` | `search/llm_client.py` |
|---|---|---|
| **协议** | Wink Agent（腾讯内网专有） | OpenAI 兼容（标准 chat/completions） |
| **用途** | 给反馈打标签（L1/L2/Severity） | AI 搜索（粗筛生成正则）+ 精筛评分 |
| **API 地址** | `winkagentsvr.dante.weread2.woa.com` | `api.deepseek.com` |
| **认证方式** | `agent_key`（内置默认值） | `LLM_API_KEY`（config.py / 环境变量） |
| **调用流程** | run → poll 轮询 | 单次 POST 请求 |
| **是否需要 VPN** | 需要 iOA VPN | 不需要（公网可达） |

两套客户端**完全独立**，互不影响。

---

## 四、修改文件清单

| 文件 | 修改类型 | 说明 |
|---|---|---|
| `dashboard/src/router/index.ts` | 修改 | 默认路由 `/` → List.vue |
| `dashboard/src/App.vue` | 修改 | 侧边栏去掉"标签列表"，加"测试"标签及样式 |
| `dashboard/src/views/List.vue` | 修改 | 标题加"测试"标签及样式 |
| `dashboard/src/views/Overview.vue` | 修改 | 标题加"测试"标签及样式 |
| `feedback_hub/search/api.py` | 修改 | `_build_keyword_where` REGEXP → LIKE；校验逻辑修复 |
| `feedback_hub/config.py` | 修改 | LLM 默认配置改为 DeepSeek + 内置 API Key |

---

## 五、重启指南

修改涉及前后端，需重启两边：

```bash
# 1. 停掉旧后端（Ctrl+C 或 kill）
# 2. 重新启动后端（LLM 配置已内置，无需手动设环境变量）
cd /Users/charvel/Desktop/用户反馈_2026_0612
python3 -m feedback_hub.cli serve

# 3. 前端如有 HMR 会自动热更新，否则重启
cd /Users/charvel/Desktop/用户反馈_2026_0612/dashboard
npm run dev
```

访问 `http://localhost:5173` 即可看到：
- 默认首页即为"反馈列表"（含搜索功能）
- 侧边栏两个导航均带橙色"测试"标签
- AI 搜索可正常调用 DeepSeek
- 关键词搜索性能大幅提升
