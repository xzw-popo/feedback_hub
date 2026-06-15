# Feedback Dashboard Implementation Plan · Part 2（业务页面 + 验收）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Part 1 (`docs/superpowers/plans/2026-05-21-feedback-dashboard.md`) 已全部完成。`dashboard/` 工程能跑 `npm run dev`，所有原子组件、API 层、composable 均已就绪并通过单测。后端 CORS 补丁已合入。

**Goal:** 实装三个业务页面（概览 / 列表 / 详情）+ 写 README + 跑通验收清单。

**Spec:** `docs/superpowers/specs/2026-05-21-feedback-dashboard-design.md` §4（页面线框）/ §10（验收标准）

---

## File Structure（本 Part 修改/创建）

**修改：**
- `dashboard/src/views/Overview.vue`（替换占位）
- `dashboard/src/views/List.vue`（替换占位）
- `dashboard/src/views/Detail.vue`（替换占位）

**新建：**
- `dashboard/README.md`

---

## Task 12：概览页 Overview.vue

**Files:**
- Modify: `dashboard/src/views/Overview.vue`（替换占位）

**业务逻辑要点：**
- 时间窗 selector：近 7 / 14 / 30 天，默认 7
- 5 张 KPI（总会话 / 自动定级率 / P0 数 / 待定占比 / 最近反馈）
- 1 张趋势图 + 3 张分布图 + 2 个表格（最新 Top 10、待定 Top 10）
- 时间窗变化时刷新 KPI / 趋势 / 分布；不刷新两个表格
- 所有数据请求并行（`Promise.all`）

- [ ] **Step 1: 写 `dashboard/src/views/Overview.vue`**

```vue
<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import KpiCard from '@/components/KpiCard.vue'
import ChartPie from '@/components/ChartPie.vue'
import ChartBar from '@/components/ChartBar.vue'
import ChartTrend from '@/components/ChartTrend.vue'
import ConversationTable from '@/components/ConversationTable.vue'
import {
  getDistribution, getTrend, listConversations,
  type DistributionResp, type TrendResp, type ConversationItem,
} from '@/api/feedback'
import { L1_COLOR, SEVERITY_COLOR } from '@/constants/labels'
import { formatTs, formatTsShort, formatPercent } from '@/utils/format'

const router = useRouter()

// 时间窗
const days = ref<7 | 14 | 30>(7)
const range = computed(() => ({
  from: dayjs().subtract(days.value, 'day').format('YYYY-MM-DD'),
  to: dayjs().format('YYYY-MM-DD'),
}))

// 数据状态
const loadingTopBlock = ref(false)
const distribution = ref<DistributionResp>({ L1: {}, L2: {}, severity: {} })
const trend = ref<TrendResp>({ granularity: 'day', buckets: [] })
const latestItems = ref<ConversationItem[]>([])
const pendingItems = ref<ConversationItem[]>([])
const loadingLatest = ref(false)
const loadingPending = ref(false)

// KPI 计算
const kpiTotal = computed(() => Object.values(distribution.value.L1).reduce((a, b) => a + b, 0))
const kpiAutoRate = computed(() => {
  const total = kpiTotal.value
  if (total === 0) return Number.NaN
  const pending = distribution.value.L1['待定'] ?? 0
  return (total - pending) / total
})
const kpiP0 = computed(() => distribution.value.severity['P0'] ?? 0)
const kpiPendingRate = computed(() => {
  const total = kpiTotal.value
  if (total === 0) return Number.NaN
  return (distribution.value.L1['待定'] ?? 0) / total
})
const kpiLatestTs = computed(() => latestItems.value[0]?.last_ts_ms ?? null)

// 颜色映射
const l1ColorMap = Object.fromEntries(
  Object.entries(L1_COLOR).map(([k, v]) => [k, v.fg]),
) as Record<string, string>
const sevColorMap = Object.fromEntries(
  Object.entries(SEVERITY_COLOR).map(([k, v]) => [k, v.fg]),
) as Record<string, string>

// 加载顶部数据块（KPI + 三图）
async function loadTopBlock() {
  loadingTopBlock.value = true
  try {
    const [d, t] = await Promise.all([
      getDistribution(range.value),
      getTrend({ ...range.value, granularity: 'day' }),
    ])
    distribution.value = d
    trend.value = t
  } finally {
    loadingTopBlock.value = false
  }
}

// 加载"最新 Top 10"
async function loadLatest() {
  loadingLatest.value = true
  try {
    const r = await listConversations({ limit: 10, offset: 0 })
    latestItems.value = r.items
  } finally {
    loadingLatest.value = false
  }
}

// 加载"待定 Top 10"
async function loadPending() {
  loadingPending.value = true
  try {
    const r = await listConversations({ L1: '待定', limit: 10, offset: 0 })
    pendingItems.value = r.items
  } finally {
    loadingPending.value = false
  }
}

watch(days, loadTopBlock)

onMounted(() => {
  void loadTopBlock()
  void loadLatest()
  void loadPending()
})

function gotoListAll() { router.push('/list') }
function gotoListPending() { router.push('/list?L1=待定') }
</script>

<template>
  <div class="page">
    <div class="overview-header">
      <h1 class="page-title">概览</h1>
      <el-radio-group v-model="days" size="default">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="14">近 14 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
      </el-radio-group>
    </div>

    <!-- KPI 卡片 -->
    <div class="kpi-grid" v-loading="loadingTopBlock">
      <KpiCard title="总会话" :value="kpiTotal" />
      <KpiCard
        title="自动定级率"
        :value="formatPercent(kpiAutoRate)"
        tooltip="非待定占比 = (总会话 - 待定数) / 总会话"
      />
      <KpiCard title="P0 数" :value="kpiP0" />
      <KpiCard
        title="待定占比"
        :value="formatPercent(kpiPendingRate)"
        tooltip="L1=待定 的占比"
      />
      <KpiCard
        title="最近反馈"
        :value="kpiLatestTs ? formatTsShort(kpiLatestTs) : '—'"
      />
    </div>

    <!-- 趋势图 -->
    <div class="card chart-card" v-loading="loadingTopBlock">
      <div class="chart-title">会话量趋势（按 L1 堆叠）</div>
      <ChartTrend :buckets="trend.buckets" />
    </div>

    <!-- 三张分布图 -->
    <div class="dist-grid" v-loading="loadingTopBlock">
      <div class="card chart-card">
        <div class="chart-title">L1 分布</div>
        <ChartPie :data="distribution.L1" :color-map="l1ColorMap" />
      </div>
      <div class="card chart-card">
        <div class="chart-title">L2 Top 10</div>
        <ChartBar :data="distribution.L2" :top-n="10" />
      </div>
      <div class="card chart-card">
        <div class="chart-title">severity 分布</div>
        <ChartPie :data="distribution.severity" :color-map="sevColorMap" />
      </div>
    </div>

    <!-- 最新 Top 10 -->
    <div class="card list-card">
      <div class="chart-title">
        最新反馈 · Top 10
        <el-button link type="primary" @click="gotoListAll">查看全部 →</el-button>
      </div>
      <ConversationTable :items="latestItems" :loading="loadingLatest" />
    </div>

    <!-- 待定 Top 10 -->
    <div class="card list-card">
      <div class="chart-title">
        待定桶 · Top 10
        <el-button link type="primary" @click="gotoListPending">查看全部 →</el-button>
      </div>
      <ConversationTable
        :items="pendingItems"
        :loading="loadingPending"
        empty-text="当前没有待定反馈 ✅"
      />
    </div>
  </div>
</template>

<style scoped>
.overview-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
}
.overview-header .page-title { margin: 0; }

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.dist-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 16px;
}
.chart-card { margin-top: 16px; }
.list-card { margin-top: 16px; }
.chart-title {
  font-size: 16px; font-weight: 600; margin-bottom: 12px;
  display: flex; align-items: center; justify-content: space-between;
}
@media (max-width: 1280px) {
  .kpi-grid { grid-template-columns: repeat(3, 1fr); }
  .dist-grid { grid-template-columns: repeat(1, 1fr); }
}
</style>
```

- [ ] **Step 2: 启动 dev，验收概览页**

Run（确保后端在跑）：`cd dashboard && npm run dev`，浏览器打开 `http://localhost:5173`

手工验收 checklist：
- [ ] 5 张 KPI 卡片显示数字（无数据则显示 0 / "—"）
- [ ] 切换近 7/14/30 天，KPI + 趋势 + 三分布同步刷新；最新 Top 10 / 待定 Top 10 不刷新
- [ ] 趋势图按 L1 堆叠
- [ ] 三张分布图配色与 §7.3 一致（A.Bug 红 / B.建议 蓝 / 待定 黄；severity P0 红…）
- [ ] 点击"最新 Top 10"任一行 → 跳详情
- [ ] 点击"待定 Top 10"的"查看全部 →" → 跳 `/list?L1=待定`
- [ ] 数据库为空时，KPI 显示 0，图表显示 ECharts "暂无数据"，"待定 Top 10"显示「当前没有待定反馈 ✅」
- [ ] Console 无报错

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/Overview.vue
git commit -m "feat(dashboard): implement Overview page (KPIs + trend + distributions + top tables)"
```

---

## Task 13：列表页 List.vue

**Files:**
- Modify: `dashboard/src/views/List.vue`（替换占位）

**业务逻辑要点：**
- URL query 同步（`q / L1 / L2 / severity / from / to / page / pageSize`）
- 默认时间窗：from=7 天前、to=今天
- "筛选"按钮才发请求；"清空"重置筛选并重新加载
- 导出 CSV 直接 `window.location.href` 下载
- 分页 + 每页大小

- [ ] **Step 1: 写 `dashboard/src/views/List.vue`**

```vue
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import dayjs from 'dayjs'
import FilterBar from '@/components/FilterBar.vue'
import ConversationTable from '@/components/ConversationTable.vue'
import { useUrlQuery } from '@/composables/useUrlQuery'
import {
  listConversations, exportCsvUrl,
  type ConversationItem, type ListParams,
} from '@/api/feedback'

const defaultFrom = dayjs().subtract(7, 'day').format('YYYY-MM-DD')
const defaultTo = dayjs().format('YYYY-MM-DD')

const url = useUrlQuery({
  q: '',
  L1: '',
  L2: '',
  severity: '',
  from: defaultFrom,
  to: defaultTo,
  page: '1',
  pageSize: '50',
})

// 给 FilterBar 用的 v-model 对象（不含 page/pageSize）
const filterState = computed({
  get() {
    return {
      q: url.q, L1: url.L1, L2: url.L2,
      severity: url.severity, from: url.from, to: url.to,
    }
  },
  set(v) {
    url.q = v.q; url.L1 = v.L1; url.L2 = v.L2
    url.severity = v.severity; url.from = v.from; url.to = v.to
  },
})

const items = ref<ConversationItem[]>([])
const total = ref(0)
const loading = ref(false)

function buildParams(): ListParams {
  const page = Number(url.page) || 1
  const pageSize = Number(url.pageSize) || 50
  return {
    q: url.q || undefined,
    L1: url.L1 || undefined,
    L2: url.L2 || undefined,
    severity: url.severity || undefined,
    from: url.from || undefined,
    to: url.to || undefined,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  }
}

async function load() {
  loading.value = true
  try {
    const r = await listConversations(buildParams())
    items.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

function onApply() {
  url.page = '1'
  void load()
}

function onClear() {
  url.q = ''; url.L1 = ''; url.L2 = ''; url.severity = ''
  url.from = defaultFrom; url.to = defaultTo
  url.page = '1'
  void load()
}

function onExport() {
  const u = exportCsvUrl({
    q: url.q || undefined,
    L1: url.L1 || undefined,
    L2: url.L2 || undefined,
    severity: url.severity || undefined,
    from: url.from || undefined,
    to: url.to || undefined,
  })
  window.location.href = u
}

function onPageChange(p: number) {
  url.page = String(p)
  void load()
}

function onSizeChange(s: number) {
  url.pageSize = String(s)
  url.page = '1'
  void load()
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page">
    <h1 class="page-title">反馈列表</h1>

    <FilterBar
      v-model="filterState"
      @apply="onApply"
      @clear="onClear"
      @export="onExport"
    />

    <div class="card table-card">
      <ConversationTable :items="items" :loading="loading" />

      <div class="pager">
        <span class="muted">共 {{ total }} 条</span>
        <el-pagination
          background
          layout="prev, pager, next, sizes"
          :total="total"
          :page-sizes="[20, 50, 100]"
          :page-size="Number(url.pageSize) || 50"
          :current-page="Number(url.page) || 1"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.table-card { margin-top: 16px; }
.pager {
  margin-top: 16px;
  display: flex; align-items: center; gap: 16px;
  justify-content: flex-end;
}
</style>
```

- [ ] **Step 2: 验收列表页**

打开 dev 浏览器：

- [ ] 默认进入 `/list`，URL query 自动补全为 `?from=...&to=...&page=1&pageSize=50`（注意 useUrlQuery 不会主动塞默认值；如果你想主动补全可加，但 spec 没要求，本任务不做）
- [ ] 选 L1=A.Bug 后点"筛选" → URL 变 `?L1=A.Bug&...`，表格刷新
- [ ] 修改日期范围后点"筛选" → URL 同步、表格刷新
- [ ] 点"清空" → 表单重置为默认时间窗
- [ ] 点表格行 → 跳详情
- [ ] 翻页、改每页大小 → URL 同步
- [ ] 点"导出 CSV" → 浏览器开始下载 `conversations.csv`
- [ ] 直接打开 `http://localhost:5173/list?L1=A.Bug&from=2026-05-15&page=2`，刷新后状态保留

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/List.vue
git commit -m "feat(dashboard): implement List page (filters + table + pagination + export)"
```

---

## Task 14：详情页 Detail.vue

**Files:**
- Modify: `dashboard/src/views/Detail.vue`（替换占位）

**业务逻辑要点：**
- 路由 props 传 `id`
- 调 `getConversation(id)`，404 显示空态
- 头信息卡 + 消息时间线
- 复制链接按钮

- [ ] **Step 1: 写 `dashboard/src/views/Detail.vue`**

```vue
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import L1Tag from '@/components/L1Tag.vue'
import SeverityTag from '@/components/SeverityTag.vue'
import { getConversation, type DetailResp } from '@/api/feedback'
import { formatTs } from '@/utils/format'

const props = defineProps<{ id: string }>()
const router = useRouter()

const data = ref<DetailResp | null>(null)
const notFound = ref(false)
const loading = ref(false)

async function load() {
  loading.value = true
  notFound.value = false
  try {
    data.value = await getConversation(props.id)
  } catch (e) {
    if ((e as { type?: string })?.type === 'NOT_FOUND') {
      notFound.value = true
    } else {
      ElMessage.error('加载失败')
    }
  } finally {
    loading.value = false
  }
}

function goBack() { router.push('/list') }

function copyLink() {
  navigator.clipboard.writeText(window.location.href).then(
    () => ElMessage.success('链接已复制'),
    () => ElMessage.error('复制失败'),
  )
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="detail-header">
      <el-button @click="goBack">← 返回列表</el-button>
      <el-button v-if="data" type="primary" @click="copyLink">📋 复制链接</el-button>
    </div>

    <div v-if="notFound" class="card empty-state">
      <h2>反馈不存在</h2>
      <p class="muted">可能已被删除或 ID 错误。</p>
      <el-button type="primary" @click="goBack">返回列表</el-button>
    </div>

    <template v-else-if="data">
      <!-- 会话头信息 -->
      <div class="card conv-head">
        <div class="conv-id font-mono muted">会话 ID: {{ data.conversation.conversation_id }}</div>
        <div class="conv-time">
          时间范围：
          <span class="font-mono">{{ formatTs(data.conversation.first_ts_ms) }}</span>
          →
          <span class="font-mono">{{ formatTs(data.conversation.last_ts_ms) }}</span>
          <span class="muted">（共 {{ data.conversation.msg_count }} 条消息）</span>
        </div>
        <el-divider />
        <div class="tag-row">
          <L1Tag :value="data.conversation.L1" />
          <span v-for="l2 in data.conversation.L2" :key="l2" class="l2-pill">{{ l2 }}</span>
          <SeverityTag :value="data.conversation.severity" />
          <span class="muted">置信度：{{ (data.conversation.confidence ?? 0).toFixed(2) }}</span>
        </div>
        <div v-if="data.conversation.reason" class="reason muted">
          理由：{{ data.conversation.reason }}
        </div>
        <el-divider />
        <div class="meta">
          <span>用户：{{ data.conversation.user_vid ?? '—' }}</span>
          <span>版本：{{ data.conversation.appversion ?? '—' }}</span>
          <span>渠道：{{ data.conversation.channel }}</span>
        </div>
      </div>

      <!-- 消息时间线 -->
      <div class="card timeline">
        <div class="chart-title">消息时间线</div>
        <div v-for="m in data.messages" :key="m.feedback_id" class="msg">
          <div class="msg-head">
            <span class="msg-seq">#{{ m.msg_seq }}</span>
            <span class="font-mono muted">{{ formatTs(m.ts_ms) }}</span>
          </div>
          <div class="msg-text">{{ m.text }}</div>
          <div class="msg-label muted">
            打标来源：<b>{{ m.source ?? '—' }}</b>
            <span v-if="m.rule_name"> · rule={{ m.rule_name }}</span>
            · L1=<L1Tag :value="m.L1" />
            <span v-if="m.L2.length"> · L2={{ m.L2.join(' / ') }}</span>
            · <SeverityTag :value="m.severity" />
            <span v-if="m.confidence != null"> · conf={{ m.confidence.toFixed(2) }}</span>
          </div>
          <div v-if="m.reason" class="msg-reason muted">理由：{{ m.reason }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-header { display: flex; justify-content: space-between; margin-bottom: 16px; }
.empty-state { text-align: center; padding: 60px 24px; }
.empty-state h2 { margin: 0 0 8px; }
.empty-state p { margin: 0 0 24px; }

.conv-head { margin-bottom: 16px; }
.conv-id { font-size: 12px; margin-bottom: 8px; }
.conv-time { font-size: 14px; }
.tag-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.l2-pill {
  padding: 2px 8px; border-radius: 4px;
  background: #e0f2fe; color: #0369a1;
  font-size: 12px;
}
.reason { margin-top: 8px; font-size: 13px; }
.meta { display: flex; gap: 24px; font-size: 13px; color: var(--text-muted); }

.timeline .chart-title { font-size: 16px; font-weight: 600; margin-bottom: 12px; }
.msg {
  border-left: 2px solid var(--border);
  padding: 8px 16px 16px 16px;
  margin-bottom: 8px;
}
.msg-head { display: flex; gap: 12px; align-items: center; margin-bottom: 6px; }
.msg-seq { font-weight: 600; color: var(--primary); }
.msg-text {
  white-space: pre-wrap;
  background: var(--bg);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
}
.msg-label { font-size: 13px; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.msg-reason { font-size: 13px; margin-top: 4px; }
</style>
```

- [ ] **Step 2: 验收详情页**

打开 dev：

- [ ] 列表页点任一行 → 跳到 `/feedback/<id>`，会话头 + 消息时间线正常
- [ ] 直接 URL 打开 `/feedback/不存在的id` → 显示「反馈不存在」
- [ ] 点"复制链接" → 1 秒内显示"链接已复制"提示
- [ ] 点"← 返回列表" → 回到 `/list`
- [ ] 多条消息时按 `msg_seq` 升序展示
- [ ] 每条消息显示**自身**的 L1/L2/severity/source（不是聚合）

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/Detail.vue
git commit -m "feat(dashboard): implement Detail page (header card + message timeline + copy link)"
```

---

## Task 15：README

**Files:**
- Create: `dashboard/README.md`

- [ ] **Step 1: 写 `dashboard/README.md`**

````markdown
# Feedback Dashboard

`feedback_hub` 的前端 Dashboard。三个页面：概览 / 反馈列表 / 反馈详情。

## 技术栈

- Vue 3.5 + TypeScript（strict）
- Vite 5
- Element Plus 2.8（浅色主题）
- ECharts 5.5（按需引入）
- vue-router 4 / axios 1.7 / dayjs

要求：Node ≥ 20。

## 目录结构

```
dashboard/
├── src/
│   ├── api/         # 后端 5 端点封装 + axios 拦截器
│   ├── components/  # 原子 / 复合 UI 组件
│   ├── composables/ # useUrlQuery / useApiHealth
│   ├── constants/   # 标签枚举与配色
│   ├── router/      # 4 条路由
│   ├── styles/      # 全局样式 + Element Plus 覆盖
│   ├── utils/       # 时间 / 百分比格式化
│   ├── views/       # Overview / List / Detail / NotFound
│   └── __tests__/   # Vitest 单测
├── index.html
├── package.json
└── vite.config.ts
```

## 本地开发

### 1. 启动后端

```bash
cd ..  # 仓库根
uvicorn feedback_hub.api:app --host 0.0.0.0 --port 8000
```

### 2. 启动前端

```bash
cd dashboard
npm install        # 首次
npm run dev        # 启动 dev server，监听 5173
```

打开 `http://localhost:5173`。

Vite 会把所有 `/api` 前缀的请求代理到 `http://localhost:8000`。后端 `feedback_hub/api.py` 已为 `localhost:5173` / `127.0.0.1:5173` 开 CORS。

## 单元测试

```bash
npm test           # 一次跑完
npm run test:watch # watch 模式
```

覆盖：`utils/format`、`api/feedback`、`composables/useUrlQuery`、`KpiCard / SeverityTag / L1Tag`。

复合组件（Charts / Table / FilterBar / 三个 view）依赖 canvas / Element Plus 内部 DOM，不写自动化测试，按 spec §10 手动验收。

## 构建与部署

```bash
npm run build      # 产物在 dashboard/dist/
```

部署建议：把 `dashboard/dist/` 静态托管在 FastAPI 同源（用 `StaticFiles` 挂到 `/`），CORS 中间件无副作用。

## 与后端的字段同步纪律

`src/constants/labels.ts` 中的 `L1_VALUES / L2_VALUES / SEVERITY_VALUES` 必须与 `feedback_hub/config.py` 的同名常量保持一致。后端改了之后**前端必须同步修改**。

如未来需要运行期同步，可由后端追加 `/api/meta/labels` 端点；本期不做。

## URL 同步（列表页）

列表页所有筛选条件（`q / L1 / L2 / severity / from / to / page / pageSize`）通过 `useUrlQuery` composable 与 URL query 双向同步。直接打开带参数的 URL 即可还原视图：

```
/list?L1=A.Bug&from=2026-05-15&to=2026-05-21&page=2
```

## 状态灯

顶栏右上角的圆点是 API 心跳指示器（每 30 秒调一次 `/api/stats/distribution`）：

- 绿：API 正常
- 红：API 不可达（后端宕机 / 网络断开）

## Spec / Plan

- 设计 spec：`../docs/superpowers/specs/2026-05-21-feedback-dashboard-design.md`
- 实施计划：
  - Part 1（基础设施）：`../docs/superpowers/plans/2026-05-21-feedback-dashboard.md`
  - Part 2（业务页面 + 验收）：`../docs/superpowers/plans/2026-05-21-feedback-dashboard-part2.md`
````

- [ ] **Step 2: Commit**

```bash
git add dashboard/README.md
git commit -m "docs(dashboard): add README"
```

---

## Task 16：跑通工程类 AC

**Files:**
- 无修改

- [ ] **Step 1: TS 编译 + lint + 测试 + 构建**

```bash
cd dashboard
npx vue-tsc --noEmit   # 期望：无错
npm run lint           # 期望：无 error / 0 warning（max-warnings 0）
npm test               # 期望：所有 Vitest 测试 PASS
npm run build          # 期望：dist/ 生成，无错
```

如有报错，逐项修复（不要跳过）。

- [ ] **Step 2: 后端测试**

```bash
cd ..
pytest feedback_hub/tests -v
```

期望：全绿（含 Task 1 的 2 条 CORS 测试）。

- [ ] **Step 3: 如有修复，commit**

```bash
git status
# 视具体改动 commit；若一切干净则跳过
```

---

## Task 17：跑通 spec §10 全部功能 AC（手动）

> 按 spec `2026-05-21-feedback-dashboard-design.md` §10 逐条核对。

**前置：** 后端 `uvicorn feedback_hub.api:app --port 8000` + 前端 `npm run dev` 两个进程都在跑。数据库里至少有 ~30 条对话方便看效果（如不足，先跑一次拉取 + 打标）。

### 概览页

- [ ] 进入 `/` 默认拉取最近 7 天数据，5 张 KPI 卡片显示数字
- [ ] 切换"近 30 天"，所有图表 + KPI 同步刷新
- [ ] 点击"最新反馈 Top 10"任一行 → 跳详情
- [ ] 点击"待定 Top 10"任一行 → 跳详情
- [ ] 点击"查看全部 →"（最新桶） → 跳 `/list`
- [ ] 点击"查看全部 →"（待定桶） → 跳 `/list?L1=待定`，且列表页 L1 已选中"待定"
- [ ] 数据库为空时（可临时用空 DB 测试），KPI 显示 0、图表显示"暂无数据"、待定卡片显示「当前没有待定反馈 ✅」

### 列表页

- [ ] 直接 URL `http://localhost:5173/list?L1=A.Bug&from=2026-05-15`，筛选条状态与表格一致
- [ ] 修改任一筛选项后点"筛选"，URL query 同步
- [ ] 点"导出 CSV" → 浏览器下载 `conversations.csv`，内容与当前筛选一致
- [ ] 点表格行跳详情页
- [ ] 翻页、改每页大小正常
- [ ] 关键词搜索 `闪退` 等中文，结果合理

### 详情页

- [ ] 直达 `/feedback/不存在id` → 显示「反馈不存在」 + 返回按钮
- [ ] 真实 ID 打开 → 头信息卡 + 时间线正常
- [ ] 时间线按 `msg_seq` 升序
- [ ] 每条消息显示**自身**的 L1 / L2 / severity / source（不是聚合后的）
- [ ] 点"复制链接" → 1 秒内提示"链接已复制"

### 全局

- [ ] 1280×720 分辨率不出现横向滚动
- [ ] 顶栏状态灯：API 正常时绿；停掉 uvicorn 后 30s 内变红
- [ ] 浏览器后退/前进可恢复列表页筛选状态
- [ ] 全程 Console 无报错或警告

### 视觉

- [ ] 整体浅色（`#f7f8fa` 背景 + 白卡片）
- [ ] severity 标签 P0 红 / P1 橙 / P2 蓝 / P3 灰
- [ ] L1 标签配色与 spec §7.3 一致
- [ ] 主按钮 `#3b82f6`，hover 加深

---

## Task 18：清理 + 总结

**Files:**
- Modify: `.gitignore`（仓库根，如必要）

- [ ] **Step 1: 检查 git status**

Run: `git status`
Expected: 工作区干净；仅 `dashboard/` 下的源文件已被追踪；`dashboard/node_modules/` 与 `dashboard/dist/` 未追踪。

如根 `.gitignore` 还没忽略 `dashboard/node_modules`，追加：

```bash
echo "dashboard/node_modules" >> .gitignore
echo "dashboard/dist" >> .gitignore
git add .gitignore
git commit -m "chore: ignore dashboard build artifacts"
```

- [ ] **Step 2: 跑一次最终冒烟**

依次：
```bash
cd dashboard
npm run lint && npm test && npm run build
cd ..
pytest feedback_hub/tests -v
```

期望：全绿。

- [ ] **Step 3: 写实施完成报告**

Create: `docs/superpowers/2026-05-21-feedback-dashboard-implementation-report.md`，参考 `2026-05-19-feedback-hub-implementation-report.md` 的格式，简要写：
1. 完成情况（与 spec §10 AC 对照）
2. 偏差点（如有）
3. 后续待办（运行期 meta 端点、L2 多选、人工修正等）

```bash
git add docs/superpowers/2026-05-21-feedback-dashboard-implementation-report.md
git commit -m "docs: feedback-dashboard implementation report"
```

---

## Self-Review

- [x] **Spec coverage**：
  - §4.1 概览页 → Task 12
  - §4.2 列表页 → Task 13
  - §4.3 详情页 → Task 14
  - §4.4 边界态 → 已散落到各 Task（Loading 用 v-loading；空状态用 emptyText / "反馈不存在"页 / 待定空文案；错误用 axios 拦截器；404 用 NotFound 页 + 详情 notFound）
  - §10 验收 → Task 16（工程）+ Task 17（功能）
  - §11 实施阶段 → 与本 plan + part1 的 Task 划分一一对应

- [x] **No placeholders**：每个 view 都有完整 `<script setup>` + `<template>` + `<style>`，复制即可运行。

- [x] **Type consistency**：
  - `useUrlQuery({...})` 在列表页用全 string 字段（含 page/pageSize 也用 string）→ 数字参数在 buildParams 里 `Number()` 转换，避免 reactive 类型混乱
  - `getConversation` 抛出的 `ApiError` 由详情页捕获并按 `type === 'NOT_FOUND'` 分支
  - `FilterBar` v-model 类型与 List.vue computed 严格匹配

- [x] **TDD 纪律**：业务页面无单测（spec 已注明手动验收），但 Task 16 / 17 提供了详细的验收清单。

- [x] **Frequent commits**：每 Task 一个 commit，共 7 个新 commit（Task 12-18）。

---

## Execution Handoff

**Plan complete and saved to:**
- Part 1: `docs/superpowers/plans/2026-05-21-feedback-dashboard.md`
- Part 2: `docs/superpowers/plans/2026-05-21-feedback-dashboard-part2.md`

**Two execution options:**

**1. Subagent-Driven (recommended)** - 每个 Task 派发一个 fresh 子 agent，Task 间 review；适合本工程（独立 Task 多）。
**2. Inline Execution** - 在当前会话连续执行，每 2-3 个 Task 一个 checkpoint；上手最快。

**回答 1 / 2，我开 craft 模式开始执行。**
