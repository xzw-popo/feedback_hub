# 用户反馈管理平台 · 前端 Dashboard 设计（阶段 2）

> 创建日期：2026-05-21
> 范围：基于 `feedback_hub/` 已有的 5 个只读 API，构建一套**浅色清爽的前端 Dashboard**，覆盖"概览 / 反馈列表 / 反馈详情"三个页面。
> 配套 spec：`docs/superpowers/specs/2026-05-19-feedback-hub-design.md`（数据中枢，已实现）。

---

## 1. 背景与目标

### 1.1 现状

阶段 1 已交付 `feedback_hub/`：

- ✅ SQLite + 4 张表（`feedback / message_label / conversation_label / label_history`）
- ✅ 拉取 + 打标流水线（`puller.py` / `tagger/`）
- ✅ FastAPI 5 个只读端点（`api.py`，详见 [§5 数据契约](#5-数据契约)）
- ⚠️ 没有任何前端，所有查询走 curl / DB Browser，**运营/产品/QA 无法自助看数**

### 1.2 本期目标

在仓库根目录新建 **`dashboard/`**，用 **Vue 3 + Element Plus + ECharts + Vite** 实现一套浅色风格的 Web Dashboard，覆盖三个页面，并完成与现有 5 个 API 的端到端对接。

具体交付：

1. `dashboard/` 目录骨架（Vite + Vue 3 + TypeScript + Element Plus + ECharts）
2. **3 个页面**：概览、反馈列表、反馈详情
3. **统一的 API 适配层**（`src/api/feedback.ts`），与后端 5 个端点 1:1 对应
4. **后端补丁**：`feedback_hub/api.py` 增加 CORSMiddleware（仅允许本地开发态）
5. **README**：本地启动、构建、部署说明

### 1.3 非目标（明确排除）

- ❌ "采集/导入"独立页面（采集走 cron / CLI）
- ❌ "标签分布" / "趋势看板"独立页面（先合并进概览，待真实使用反馈再决定是否拆分）
- ❌ "立即拉取"按钮（不做，避免引入后端写接口）
- ❌ "重新打标 / 人工修正"功能（schema 预留 `label_history`，下一期再做）
- ❌ 移动端适配（最低分辨率 1280×720，桌面浏览器为主）
- ❌ 鉴权 / 多租户（内网工具）
- ❌ 国际化（仅简体中文）
- ❌ 暗色模式（本期只做浅色）

---

## 2. 用户与场景

### 2.1 主要用户

| 角色 | 占比 | 主要诉求 |
|---|---|---|
| **你（产品/运营）** | 60% | 每天/每周看一次大盘，捕捉 P0、识别趋势 |
| **QA / 开发** | 30% | 拿到 Bug 反馈链接，跳详情排查 |
| **临时旁观者**（leader 等） | 10% | 偶尔打开看一眼整体情况 |

### 2.2 典型用户流（核心三条）

**流 A · 每日巡检（5 分钟）**
> 打开首页 → 看 KPI 是否有 P0 → 看 7 天趋势是否突增 → 看"待定 Top 10"决定是否需要人工判一下 → 关闭。

**流 B · 排查具体 Bug（2 分钟）**
> 列表页 → 筛选 `L1=A.Bug + severity=P0 + 最近 7 天` → 点行 → 详情页 → 复制 URL 发给开发同学。

**流 C · 周报取数（5 分钟）**
> 列表页 → 筛选时间范围 → 点导出 CSV → 在 Excel 里整理。

---

## 3. 信息架构

### 3.1 路由表

| 路由 | 页面 | 入口 |
|---|---|---|
| `/` | 概览（首页） | 左导航默认项 |
| `/list` | 反馈列表 | 左导航 + 概览页"查看全部" |
| `/feedback/:id` | 反馈详情 | 列表点击行 / 概览页"待定 Top 10"点击行 / URL 直达 |
| `*` | 404 | 兜底 |

**关键决策**：详情页用**独立路由**而非抽屉，URL 可分享给开发同学。

### 3.2 全局布局

```
┌────────────────────────────────────────────────────────────┐
│ Header（高 56px，浅灰底）                                   │
│   左：Logo「反馈台」    右：状态灯 ●（健康/降级）            │
├──────────┬─────────────────────────────────────────────────┤
│ 左导航    │                                                  │
│ 240px    │            主内容区（白底卡片）                  │
│          │                                                  │
│ ▸ 概览   │                                                  │
│ ▸ 列表   │                                                  │
│          │                                                  │
└──────────┴─────────────────────────────────────────────────┘
```

- **不设顶栏全局时间筛选**：概览页有自己的时间窗 selector，列表页有筛选条；全局放反而把状态搞混
- **不设"立即拉取"按钮**：见 §1.3
- **状态灯**：右上角圆点，绿=API 正常，红=API 不可达（只调一次 `/api/stats/distribution?from=today` 当心跳）

---

## 4. 页面线框

### 4.1 页面 1：概览（`/`）

```
┌─时间窗 selector（右对齐） ──────────────────┐
│                                  [近 7 天 ▾]│
└────────────────────────────────────────────┘

┌─KPI Cards（5 列等宽，高 96px） ─────────────────────────────────────┐
│ 总会话    │ 规则命中率 │ P0 数  │ 待定占比 │ 最近反馈           │
│  142     │   47%      │   6    │   22%   │ 2026-05-21 17:02   │
└──────────┴────────────┴────────┴─────────┴────────────────────┘

┌─时间趋势（卡片 1，宽 100%，高 320px） ──────────────────────────────┐
│ 标题：会话量趋势（按 L1 堆叠）                                       │
│ ECharts 折线/堆叠柱（granularity=day）                              │
└────────────────────────────────────────────────────────────────────┘

┌─L1 分布（卡片 2，1/3 宽）─┬─L2 Top 10（卡片 3，1/3 宽）─┬─severity 分布（卡片 4，1/3 宽）─┐
│ 饼图（5 个 L1）            │ 横向条形图                  │ 饼图（P0/P1/P2/P3）             │
└──────────────────────────┴─────────────────────────────┴─────────────────────────────────┘

┌─最新反馈 Top 10（卡片 5，宽 100%）──────────────────────────┐
│ 时间 │ 反馈片段（2行截断） │ L1+L2 │ severity │ 平台·版本   │
│ ⋮（10 行，hover 高亮，点击跳详情）                          │
│ 右上角：[查看全部 →]（跳 /list）                            │
└────────────────────────────────────────────────────────────┘

┌─待定桶 Top 10（卡片 6，宽 100%）──────────────────────────┐
│ 时间 │ 反馈片段 │ severity │ 平台·版本                      │
│ ⋮（10 行，调 /api/conversations?L1=待定&limit=10）         │
│ 右上角：[查看全部 →]（跳 /list?L1=待定）                    │
└──────────────────────────────────────────────────────────┘
```

**交互细节**：
- 时间窗 selector 选项：`近 7 天 / 近 14 天 / 近 30 天`，默认 7 天
- 时间窗变化时刷新 KPI / 趋势图 / 三个分布图；**不影响"最新反馈" / "待定桶"**（这两个永远显示最新）
- 所有图表 loading 期间显示 Element Plus `v-loading`
- 任何卡片点击图例可隐藏对应类目（ECharts 默认行为）
- 卡片间距 16px，外边距 24px

**KPI 计算口径**：

| KPI | 数据源 | 公式 |
|---|---|---|
| 总会话 | `/api/stats/distribution` | `sum(L1.values)` |
| 规则命中率 | `/api/conversations` 抽样 | 详见 §5.6（前端聚合） |
| P0 数 | `/api/stats/distribution` | `severity.P0`（默认 0） |
| 待定占比 | `/api/stats/distribution` | `L1["待定"] / sum(L1.values)`（百分比） |
| 最近反馈 | `/api/conversations?limit=1` | `items[0].last_ts_ms` 格式化 |

> ⚠️ "规则命中率"由前端用 `/api/conversations?limit=200` 抽样后端最近 200 条会话，统计 `messages[].source == "rule"` 占比。**这是采样近似值**，会在 KPI 卡片右下角加 tooltip 说明。后续如要精确值，给后端加端点。

---

### 4.2 页面 2：反馈列表（`/list`）

```
┌─筛选条（一行，高 56px，可换行） ────────────────────────────────────┐
│ [关键词搜索      ] [L1▾] [L2▾] [severity▾] [日期范围 ▾]              │
│                                       [筛选] [清空] [导出 CSV]      │
└────────────────────────────────────────────────────────────────────┘

┌─表格 ──────────────────────────────────────────────────────────────┐
│ 时间          │ 反馈片段（截断）       │ L1+L2 标签 │ severity │ 平台·版本 │
│ 2026-05-21 17:02 │ 刚才打字直接闪退了... │ A.Bug+性能 │ P0 (红)  │ Android 1.2.3│
│ ⋮（每行点击跳详情，hover 高亮，最多 50 行）                            │
└────────────────────────────────────────────────────────────────────┘

┌─分页 ──────────────────────────────────────────────────────────────┐
│ 共 142 条 · [<] 1 2 3 ... [>] · 每页 [50 ▾]                         │
└────────────────────────────────────────────────────────────────────┘
```

**交互细节**：
- **URL 同步**：所有筛选条件写到 URL query（`?L1=A.Bug&from=2026-05-15&to=2026-05-21&page=2`），刷新页面状态保留，分享链接给同事可还原视图
- **L1 / L2 / severity 选项**：从 `feedback_hub/config.py` 的常量同步到前端（编译期写死）；如要动态，加 `/api/meta/labels` 端点（**本期不做**）
- **关键词搜索**：调 `/api/conversations?q=xxx`（后端走 `feedback.text LIKE %xxx%`）
- **日期范围**：Element Plus `el-date-picker` daterange，传 `from` / `to`（YYYY-MM-DD）
- **导出 CSV**：直接 `window.location.href = '/api/export.csv?...同筛选...'`，浏览器原生下载
- **每页大小**：20 / 50 / 100，默认 50
- **行渲染**：反馈片段 2 行截断；severity 用彩色 Tag（P0=红，P1=橙，P2=蓝，P3=灰）；L1+L2 用 Tag 串
- **点击行**：`router.push(/feedback/${id})`，整行可点（不限于"详情"按钮）

---

### 4.3 页面 3：反馈详情（`/feedback/:id`）

```
┌─返回按钮 ──────────────────────────────────────────────────────────┐
│ [← 返回列表]                                       [复制链接 📋]    │
└────────────────────────────────────────────────────────────────────┘

┌─会话头信息卡片（白底，宽 100%） ────────────────────────────────────┐
│ 会话 ID：a3f1c8e9d2b4                                                │
│ 时间范围：2026-05-21 14:30 → 14:42（共 12 分钟，3 条消息）           │
│ ────────────────────────────────────────────                        │
│ 标签：[A.Bug] [输入核心] [性能]    severity：[P0]   置信度：0.90    │
│ 打标来源：aggregated（聚合）                                         │
│ 理由：P0 关键词 闪退                                                  │
│ ────────────────────────────────────────────                        │
│ 用户：xxx · 平台：Android · 版本：1.2.3 · 渠道：openapi              │
└────────────────────────────────────────────────────────────────────┘

┌─消息时间线（按 msg_seq 升序） ──────────────────────────────────────┐
│ #0  2026-05-21 14:30:00                                             │
│   📝 完整文本                                                        │
│   ─                                                                 │
│   规则打标：rule=keyword_crash · L1=A.Bug · L2=性能 · P0 · conf=0.95│
│   理由：P0 关键词 闪退                                                │
│                                                                     │
│ #1  2026-05-21 14:35:21                                             │
│   📝 ……                                                              │
│   LLM 打标：L1=A.Bug · L2=输入核心 · P1 · conf=0.80                 │
│   理由：……                                                            │
│                                                                     │
│ #2  ……                                                              │
└────────────────────────────────────────────────────────────────────┘
```

**交互细节**：
- **数据源**：`GET /api/conversations/{id}`，一次拉到 `conversation` + 所有 `messages`
- **404 兜底**：API 返回 404 时显示"反馈不存在"页 + 返回列表按钮
- **复制链接**：调 `navigator.clipboard.writeText(window.location.href)` + 浮层提示
- **不做"相关反馈"侧栏**（之前 brainstorm 时提过，本期砍掉，因为后端没有同 L1/同平台筛选的高效端点；要做就得前端跨多次调用 + 自己 join，复杂度高 ROI 低）
- 消息时间线每条消息展示**自身**的 `message_label`（不是聚合后的），便于看到打标差异
- 长文本（>500 字）默认折叠，点击"展开"

---

### 4.4 边界态

| 态 | 视觉 | 触发 |
|---|---|---|
| **Loading** | Element Plus `v-loading`（白底半透明 + spinner） | 任何接口请求中 |
| **空状态** | 灰色插画 + "暂无数据" + 操作建议 | 当前筛选无结果 / 数据库为空 |
| **错误** | 红色 Alert + "请求失败" + [重试] | API 5xx / 网络错 |
| **404 路由** | 大字「404」+ [返回首页] | 未匹配路由 |
| **404 资源** | "反馈不存在" + [返回列表] | 详情页 conv_id 不存在 |
| **API 不可达** | 顶栏状态灯变红 + Toast | 心跳接口失败 |

---

## 5. 数据契约

### 5.1 后端端点（已实现，本期不改协议）

复述 `feedback_hub/api.py` 的 5 个端点（详见阶段 1 spec §6）：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/conversations` | 会话列表 |
| GET | `/api/conversations/{id}` | 单会话详情（含所有消息） |
| GET | `/api/stats/distribution` | L1 / L2 / severity 分布 |
| GET | `/api/stats/trend` | 时间趋势 |
| GET | `/api/export.csv` | CSV 导出 |

**前端只调这 5 个端点，不调任何其它**（包括不读 SQLite 文件）。

### 5.2 通用查询参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `from` | `YYYY-MM-DD` 或带时分秒 | 起始（含） |
| `to` | 同上 | 结束（含到 23:59:59） |
| `L1` | enum | 见 §5.5 |
| `L2` | string | 单个 L2 名（后端 `LIKE %L2%` 匹配） |
| `severity` | `P0`/`P1`/`P2`/`P3` | |
| `q` | string | 文本关键词 |
| `limit` | int 1–500 | 默认 50 |
| `offset` | int ≥ 0 | 默认 0 |

### 5.3 列表响应（`/api/conversations`）

```ts
interface ListResp {
  total: number
  items: ConversationItem[]
}

interface ConversationItem {
  conversation_id: string
  L1: string
  L2: string[]            // 数组（后端已拆 '|'）
  severity: 'P0' | 'P1' | 'P2' | 'P3'
  confidence: number
  reason: string | null
  msg_count: number
  first_ts_ms: number     // unix 毫秒
  last_ts_ms: number
  user_vid: string | null
  appversion: string | null
  channel: string
  preview_text: string    // 首条消息的前 80 字
}
```

### 5.4 详情响应（`/api/conversations/{id}`）

```ts
interface DetailResp {
  conversation: ConversationItem  // preview_text 为空
  messages: MessageItem[]
}

interface MessageItem {
  feedback_id: string
  msg_seq: number
  ts_ms: number
  text: string
  appversion: string | null
  platform: string | null
  L1: string | null
  L2: string[]
  severity: 'P0' | 'P1' | 'P2' | 'P3' | null
  confidence: number | null
  reason: string | null
  source: 'rule' | 'llm' | null
  rule_name: string | null
}
```

### 5.5 枚举常量

从 `feedback_hub/config.py` 复制到 `dashboard/src/constants/labels.ts`（编译期同步，避免运行时再调 API）：

```ts
export const L1_VALUES = ['A.Bug', 'B.建议', 'C.咨询', 'D.情绪', 'E.无效', '待定'] as const
export const SEVERITY_VALUES = ['P0', 'P1', 'P2', 'P3'] as const
export const SEVERITY_COLOR: Record<typeof SEVERITY_VALUES[number], string> = {
  P0: '#ef4444',  // red-500
  P1: '#f97316',  // orange-500
  P2: '#3b82f6',  // blue-500
  P3: '#9ca3af',  // gray-400
}
export const L1_COLOR: Record<string, string> = {
  'A.Bug':  '#ef4444',
  'B.建议': '#3b82f6',
  'C.咨询': '#9ca3af',
  'D.情绪': '#a78bfa',
  'E.无效': '#d1d5db',
  '待定':   '#fbbf24',
}
// L2_VALUES 较多，启动时跑一个脚本从 config.py 同步
```

> **同步纪律**：后端 `config.py` 改了 L1/L2 值，前端必须同步修改。本期不做"运行期拉取"是因为这五个端点没必要再加一个 meta 端点，且 L1/L2 半年都未必动一次。

### 5.6 KPI"规则命中率"采样口径

```ts
// 拉最近 200 条会话，遍历 messages 数组（其实只有 list 端点，不会返回 messages）
// 实际方案：取最近 200 条会话，对每条调 /api/conversations/{id} ❌（200 次请求，太重）
//
// 折衷方案（本期采用）：
//   把 KPI 的"规则命中率"改成"会话级指标"——但 conversation_label.source 永远是 'aggregated'，无意义
//   所以这条 KPI 改为：
//   "规则命中率" = 概览时间窗内 L1≠'待定' 的会话占比
//   口径说明（tooltip）：待定 = 规则未命中且 LLM 未给出确定结论；非待定即可视为"系统自动定级成功"
//   这与"规则命中率"的字面有偏差，因此 KPI 标签改为：
//
//   ✅ 实际 KPI 名："自动定级率"（替代"规则命中率"）
//   公式： (sum(L1.values) - L1["待定"]) / sum(L1.values)
```

**结论**：把 KPI 卡片标题从"规则命中率"改成"**自动定级率**"，公式如上，单次 `/api/stats/distribution` 即可计算，**无需采样**。tooltip 写明定义。

---

## 6. 技术栈与目录结构

### 6.1 技术栈

| 层 | 选型 | 版本 | 说明 |
|---|---|---|---|
| 构建 | Vite | ^5.4 | dev server + build |
| 框架 | Vue | ^3.5 | Composition API + `<script setup>` |
| 语言 | TypeScript | ^5.6 | 严格模式（`strict: true`） |
| 路由 | vue-router | ^4.4 | history mode |
| UI | Element Plus | ^2.8 | 主色覆盖（见 §7） |
| 图表 | ECharts | ^5.5 | 按需引入：`PieChart` `LineChart` `BarChart` |
| HTTP | axios | ^1.7 | 拦截器统一处理错误 + baseURL |
| 工具 | dayjs | ^1.11 | 时间格式化 |
| 测试 | Vitest | ^2.1 | 单元；不强制覆盖率 |
| Lint | ESLint + Prettier | 默认 | 团队约定 |

**Node 要求**：v20+ ✅（已确认本机 v20.20.2）

### 6.2 目录结构

```
用户反馈_2026_0519/
├── feedback_hub/                # 后端（不动，仅给 api.py 加 CORS）
├── dashboard/                   # 本期新建
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── .eslintrc.cjs
│   ├── .prettierrc
│   ├── .gitignore               # node_modules / dist
│   ├── README.md
│   ├── public/
│   │   └── favicon.svg
│   └── src/
│       ├── main.ts              # app 入口 + 注入 Element Plus + router
│       ├── App.vue              # 全局布局：Header + Sider + RouterView
│       ├── router/
│       │   └── index.ts         # 4 条路由
│       ├── api/
│       │   ├── http.ts          # axios 实例 + 拦截器
│       │   └── feedback.ts      # 5 个端点的封装
│       ├── constants/
│       │   └── labels.ts        # L1/L2/severity 枚举 + 配色
│       ├── composables/
│       │   ├── useUrlQuery.ts   # URL ↔ ref 双向同步
│       │   └── useApiHealth.ts  # 状态灯心跳
│       ├── views/
│       │   ├── Overview.vue
│       │   ├── List.vue
│       │   ├── Detail.vue
│       │   └── NotFound.vue
│       ├── components/
│       │   ├── KpiCard.vue
│       │   ├── ChartPie.vue
│       │   ├── ChartBar.vue
│       │   ├── ChartTrend.vue
│       │   ├── ConversationTable.vue   # 列表页 + 概览的"最新/待定"复用
│       │   ├── FilterBar.vue
│       │   ├── SeverityTag.vue
│       │   └── L1Tag.vue
│       ├── styles/
│       │   ├── element-overrides.scss  # 主题色覆盖
│       │   └── global.scss
│       └── utils/
│           ├── format.ts        # 时间 / 数字格式化
│           └── csv.ts           # 触发下载
└── docs/superpowers/specs/
    └── 2026-05-21-feedback-dashboard-design.md  # 本文件
```

### 6.3 与后端联调

- **dev**：vite proxy `/api` → `http://localhost:8000`（vite.config.ts 配）
- **生产**：把 `dist/` 静态托管在 FastAPI 同源（用 `StaticFiles` 挂到 `/`），无需 CORS
- **CORS**：`api.py` 加 dev 期白名单（见 §8），生产同源时该中间件无副作用

---

## 7. 视觉规范

### 7.1 色板（浅色清爽）

| 用途 | 颜色 | 备注 |
|---|---|---|
| 主色（强调按钮、链接） | `#3b82f6` | tailwind blue-500 |
| 主色 hover | `#2563eb` | blue-600 |
| 成功 / 健康 | `#10b981` | emerald-500（状态灯） |
| 警告 | `#f59e0b` | amber-500 |
| 错误 / P0 | `#ef4444` | red-500 |
| 中性 / 边框 | `#e5e7eb` | gray-200 |
| 文本主 | `#1f2937` | gray-800 |
| 文本次 | `#6b7280` | gray-500 |
| 背景（body） | `#f7f8fa` | 极浅灰 |
| 背景（卡片） | `#ffffff` | |

**Element Plus 主题覆盖**：仅改 `--el-color-primary` 为 `#3b82f6`，其它跟默认。

### 7.2 字号 / 字体 / 间距

| 项 | 值 |
|---|---|
| 字体 | `system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif` |
| 数字字体（KPI / 时间） | `"JetBrains Mono", "SF Mono", monospace` |
| H1（页面标题） | 20px / 600 |
| H2（卡片标题） | 16px / 600 |
| 正文 | 14px / 400 |
| 辅助 | 12px / 400 |
| 卡片圆角 | 8px |
| 卡片阴影（默认） | 无 |
| 卡片阴影（hover） | `0 2px 8px rgba(0,0,0,0.06)` |
| 卡片间距 | 16px |
| 页面外边距 | 24px |

### 7.3 标签徽章

| L1 | 配色（前景/背景） |
|---|---|
| A.Bug | `#ef4444` / `#fee2e2` |
| B.建议 | `#3b82f6` / `#dbeafe` |
| C.咨询 | `#6b7280` / `#f3f4f6` |
| D.情绪 | `#8b5cf6` / `#ede9fe` |
| E.无效 | `#9ca3af` / `#f3f4f6` |
| 待定 | `#d97706` / `#fef3c7` |

severity 同色但更鲜艳，配色见 §5.5。

---

## 8. 后端兼容改动（仅一处）

### 8.1 CORS

修改 `feedback_hub/api.py`，在 `create_app` 内挂 `CORSMiddleware`：

```python
# feedback_hub/api.py（增量）
from fastapi.middleware.cors import CORSMiddleware

def create_app(db_path: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="feedback_hub", version="1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    # ... 原有逻辑不变
```

**说明**：
- 生产部署时若 `dashboard/dist/` 被 FastAPI 同源托管，`Origin` header 与后端同源，CORS 中间件不影响。
- 仅放开 `GET`：现有 5 个端点都是 GET，且本期前端不会发起任何写请求。
- 生产环境若部署到不同域名，再扩 `allow_origins`。

### 8.2 不改 5 个端点的协议

API 字段、参数语义、错误码全部保持 §5 描述，与阶段 1 spec 一致。前端任何"想要新字段"的诉求都先尝试用现有响应满足；实在不行再立项扩端点（**本期一律不做**）。

---

## 9. 关键交互与边界

### 9.1 URL 状态同步（仅列表页）

| 字段 | URL key | 默认 |
|---|---|---|
| 关键词 | `q` | `''` |
| L1 | `L1` | `''` |
| L2 | `L2` | `''` |
| severity | `severity` | `''` |
| 起始日期 | `from` | 7 天前 |
| 结束日期 | `to` | 今天 |
| 当前页 | `page` | `1` |
| 每页大小 | `pageSize` | `50` |

实现：用 `useUrlQuery` composable，在 `<script setup>` 顶部一行调用，自动双向同步。

### 9.2 错误处理

`src/api/http.ts` axios 拦截器：

```ts
// 伪代码
http.interceptors.response.use(
  (resp) => resp,
  (err) => {
    if (err.response?.status === 404) return Promise.reject({type: 'NOT_FOUND', detail: err.response.data?.detail})
    if (err.response?.status >= 500) {
      ElMessage.error('服务异常，请稍后重试')
      return Promise.reject({type: 'SERVER_ERROR'})
    }
    if (!err.response) {
      ElMessage.error('网络错误，请检查后端服务')
      return Promise.reject({type: 'NETWORK_ERROR'})
    }
    return Promise.reject({type: 'UNKNOWN', detail: err.message})
  }
)
```

### 9.3 性能 / 节流

- 列表页筛选条件变更后，**用户点"筛选"按钮才发请求**，不做实时联动（避免每选一个 L1 就发一次）
- 关键词输入框无 debounce（不实时搜索）
- 概览页所有图表**并行请求**（`Promise.all`），不串行
- 详情页消息列表如果 >50 条，前端虚拟滚动（用 `el-table` 自带虚拟模式）

### 9.4 时间格式

| 场景 | 格式 |
|---|---|
| 列表 / 详情头 | `YYYY-MM-DD HH:mm` |
| KPI"最近反馈" | `MM-DD HH:mm` |
| 趋势图坐标 | `MM-DD`（day）/ `MM-DD HH:00`（hour） |
| URL `from/to` | `YYYY-MM-DD`（不带时分） |

---

## 10. 验收标准

### 10.1 功能 AC

**概览页**：
- [ ] 进入 `/` 默认拉取最近 7 天数据，5 张 KPI 卡片显示数字
- [ ] 切换时间窗到"近 30 天"，所有图表与 KPI 卡片同步刷新
- [ ] 点击"最新反馈 Top 10"任一行，跳转到对应详情页
- [ ] 点击"待定 Top 10"任一行，跳转到对应详情页
- [ ] 点击"查看全部 →"跳转到列表页（待定桶链接带 `?L1=待定`）
- [ ] 数据库为空时，5 张图表显示"暂无数据"占位

**列表页**：
- [ ] URL 直接打开 `/list?L1=A.Bug&from=2026-05-15`，筛选条与表格状态一致
- [ ] 修改任一筛选项后，URL query 同步更新
- [ ] 点击"导出 CSV"触发浏览器下载
- [ ] 点击表格行跳转详情页
- [ ] 分页、每页大小切换正常
- [ ] 关键词包含特殊字符（`%` `_`）时不报错（后端会自己处理转义？需验证；不行就前端转义）

**详情页**：
- [ ] URL 直达 `/feedback/<不存在>`，显示 "反馈不存在" + 返回按钮
- [ ] 消息时间线按 `msg_seq` 升序展示
- [ ] 每条消息显示自身 `message_label`（不是聚合后的）
- [ ] 复制链接按钮点击后 1 秒内显示"已复制"提示

**全局**：
- [ ] 所有页面在 1280×720 以上分辨率不出现横向滚动条
- [ ] 顶栏状态灯：API 正常时为绿色；停掉 uvicorn 后 30 秒内变红
- [ ] 浏览器后退/前进可正常恢复列表页筛选状态

### 10.2 工程 AC

- [ ] `cd dashboard && npm install && npm run dev` 能成功启动并打开 `http://localhost:5173`
- [ ] `npm run build` 在 `dist/` 产生静态资源，无 TS 报错
- [ ] `npm run lint` 通过
- [ ] 所有页面无浏览器 Console 报错或警告
- [ ] `dashboard/README.md` 包含：依赖、本地启动、构建、与后端联调步骤
- [ ] `feedback_hub/api.py` 加 CORSMiddleware 后，原有 pytest 全绿（不影响后端测试）

### 10.3 视觉 AC

- [ ] 整体浅色（#f7f8fa 背景 + 白卡片），无任何深色面板
- [ ] severity 标签 P0=红、P1=橙、P2=蓝、P3=灰，与 §5.5 一致
- [ ] L1 标签配色与 §7.3 一致
- [ ] 主按钮颜色为 `#3b82f6`，hover 加深

---

## 11. 实施阶段（高层）

> 详细任务由后续 writing-plans 阶段产出（`docs/superpowers/plans/2026-05-21-feedback-dashboard.md`）。

| 阶段 | 内容 | 预计 |
|---|---|---|
| P1 | 骨架：`dashboard/` 目录 + Vite + 路由 + 全局布局 + axios + CORS 后端补丁 | 0.5 天 |
| P2 | 共用组件：`KpiCard / ConversationTable / Severity-/L1-Tag / 三个 Chart 组件` | 0.5 天 |
| P3 | 概览页：5 张 KPI + 趋势 + 三分布 + 最新/待定列表 | 1 天 |
| P4 | 列表页：筛选条 + 表格 + 分页 + 导出 + URL 同步 | 0.5 天 |
| P5 | 详情页：头信息卡 + 消息时间线 + 404 兜底 + 复制链接 | 0.5 天 |
| P6 | 自检：跑通 §10 所有 AC + README | 0.5 天 |

合计：~3.5 工作日。

---

## 12. 风险与已知问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| L2 选项较多（数十个），下拉框难选 | 列表页 L2 筛选体验差 | 用 `el-select` 带搜索；后续如果 >50 个考虑级联（按 L1 → L2） |
| 关键词搜索后端走 `LIKE %q%` 全表扫 | 数据量大时慢（>10w 行可能秒级） | 当前 1 年 70w 行实测可接受；后续如慢再考虑 FTS5 |
| 详情页消息很多时（>50）渲染慢 | 用户感知卡顿 | `el-table` 虚拟滚动 |
| ECharts 包体大（500KB+） | 首屏加载慢 | 按需引入；启用 gzip；放 CDN（备选） |
| 时区 | 后端 `ts_ms` 是 UTC？本地？ | 实测 `feedback_hub` 用 `datetime.fromtimestamp(ts/1000)` 走本地时区，前端也用 `new Date(ts)`，两边一致即可；统一注释说明 |
| 当 L1/L2 的 config.py 后端变了，前端忘改 | 筛选下拉项缺 / 多 | 在 `dashboard/README.md` 写明同步纪律；后续可加 `/api/meta/labels` 端点（非本期） |
| "待定 Top 10"调 `L1=待定` 时如果待定为空，卡片空 | 视觉不和谐 | 显示"当前没有待定反馈 ✅" |

---

## 13. 自检（spec 通过性）

按 brainstorming skill 的标准复核：

- [x] **目标清晰**：3 个页面，5 个端点，浅色清爽，3.5 天交付
- [x] **非目标明确**：见 §1.3，不留模糊地带
- [x] **数据契约对齐**：§5 与 `feedback_hub/api.py` 1:1 校对
- [x] **后端改动最小**：只加 CORS 中间件
- [x] **每个页面有线框**：§4.1 / §4.2 / §4.3
- [x] **每个页面有 AC**：§10.1
- [x] **决策点已闭环**：方案 A（3 页）+ 待定桶 B + 立即拉取 A + 详情页独立路由 + Element Plus
- [x] **风险列项**：§12，每项都有缓解
- [x] **暴露口径分歧**：§5.6 把"规则命中率"改名"自动定级率"，避免误导

---

**spec 完。**

下一步：用 `writing-plans` skill 把 §11 的 6 个阶段拆成具体可执行的 plan（每步 2-5 分钟、TDD、frequent commits），落到 `docs/superpowers/plans/2026-05-21-feedback-dashboard.md`。
