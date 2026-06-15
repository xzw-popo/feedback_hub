# Feedback Dashboard Implementation Plan · Part 1（基础设施 + 组件）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `dashboard/` 下搭起 Vite + Vue 3 工程骨架；对接后端补 CORS；写完所有可复用的工具函数、API 层、composable、原子组件，并通过 Vitest 单测。完成后即可在 Task Part 2 中拼装三个业务页面。

**Architecture:** SPA。本 Part 不涉及业务页面，只做"地基"：后端 CORS、工程脚手架、常量、样式、API 客户端、URL 同步、状态灯心跳、原子组件（KpiCard / SeverityTag / L1Tag / 三个 Chart / 表格 / 筛选条）。每个具备明确逻辑的单元都有单测。

**Tech Stack:** Node 20 / Vite 5 / Vue 3.5 / TypeScript 5.6（strict）/ Element Plus 2.8 / ECharts 5.5 / axios 1.7 / dayjs 1.11 / Vitest 2.1 / vue-router 4.4

**Spec:** `docs/superpowers/specs/2026-05-21-feedback-dashboard-design.md`

**Companion plan:** `docs/superpowers/plans/2026-05-21-feedback-dashboard-part2.md`（业务页面 + 验收）

---

## File Structure

**新建（本 Part 完成后磁盘上应有）：**

```
dashboard/
├── .gitignore
├── .eslintrc.cjs
├── .prettierrc
├── README.md             # part2 写
├── index.html
├── package.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── vitest.config.ts
├── public/favicon.svg
└── src/
    ├── main.ts
    ├── App.vue
    ├── router/index.ts
    ├── api/{http.ts, feedback.ts}
    ├── constants/labels.ts
    ├── composables/{useUrlQuery.ts, useApiHealth.ts}
    ├── views/{Overview.vue, List.vue, Detail.vue, NotFound.vue}  # 占位，part2 实装
    ├── components/{KpiCard.vue, ChartPie.vue, ChartBar.vue, ChartTrend.vue,
    │               ConversationTable.vue, FilterBar.vue,
    │               SeverityTag.vue, L1Tag.vue}
    ├── styles/{element-overrides.scss, global.scss}
    ├── utils/format.ts
    └── __tests__/{format,feedback-api,useUrlQuery,KpiCard,SeverityTag,L1Tag}.test.ts
```

**修改：**
- `feedback_hub/api.py`：加 CORSMiddleware

---

## 测试策略

| 层 | 测试方式 | 测试位置 |
|---|---|---|
| 工具函数（`utils/format`） | Vitest 单测 | Task 4 |
| API 层（`api/feedback`） | Vitest + axios mock | Task 5 |
| Composable（`useUrlQuery`） | Vitest + memory router | Task 6 |
| 原子组件（KpiCard/SeverityTag/L1Tag） | Vitest + `@vue/test-utils` | Task 7 |
| 复合组件（Charts/Table/FilterBar） | 不写自动化测试，依赖 Part 2 手动 AC | — |
| 后端 CORS 补丁 | pytest（含 2 条新增） | Task 1 |

---

## Task 1：后端 CORS 补丁

**Files:**
- Modify: `feedback_hub/api.py`
- Modify: `feedback_hub/tests/test_api.py`

- [ ] **Step 1: 在 `feedback_hub/tests/test_api.py` 末尾追加 CORS 测试**

```python
def test_cors_allows_localhost_5173(client):
    r = client.options(
        "/api/conversations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_blocks_unknown_origin(client):
    r = client.get(
        "/api/conversations",
        headers={"Origin": "http://evil.example.com"},
    )
    assert r.status_code == 200
    headers_lower = {k.lower() for k in r.headers.keys()}
    assert "access-control-allow-origin" not in headers_lower
```

- [ ] **Step 2: 运行新测试确认 FAIL**

Run: `pytest feedback_hub/tests/test_api.py::test_cors_allows_localhost_5173 feedback_hub/tests/test_api.py::test_cors_blocks_unknown_origin -v`
Expected: FAIL（中间件未挂载）

- [ ] **Step 3: 修改 `feedback_hub/api.py`**

在文件顶部 import 区追加：

```python
from fastapi.middleware.cors import CORSMiddleware
```

修改 `create_app` 函数体开头，插入 CORS 中间件（在 `app = FastAPI(...)` 之后立刻）：

```python
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
    # ...原有逻辑保持不变
```

- [ ] **Step 4: 运行所有后端测试**

Run: `pytest feedback_hub/tests -v`
Expected: PASS（含原有所有用例 + 新增 2 条 CORS）

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/api.py feedback_hub/tests/test_api.py
git commit -m "feat(api): add CORS middleware for dashboard dev (localhost:5173)"
```

---

## Task 2：dashboard 工程骨架

**Files:**
- Create: `dashboard/{package.json, vite.config.ts, vitest.config.ts, tsconfig.json, tsconfig.node.json, .eslintrc.cjs, .prettierrc, .gitignore, index.html, public/favicon.svg}`

- [ ] **Step 1: 写 `dashboard/package.json`**

```json
{
  "name": "feedback-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint . --ext .ts,.vue --max-warnings 0"
  },
  "dependencies": {
    "axios": "^1.7.7",
    "dayjs": "^1.11.13",
    "echarts": "^5.5.1",
    "element-plus": "^2.8.8",
    "vue": "^3.5.13",
    "vue-router": "^4.4.5"
  },
  "devDependencies": {
    "@types/node": "^20.16.10",
    "@typescript-eslint/eslint-plugin": "^7.18.0",
    "@typescript-eslint/parser": "^7.18.0",
    "@vitejs/plugin-vue": "^5.1.4",
    "@vue/test-utils": "^2.4.6",
    "eslint": "^8.57.1",
    "eslint-plugin-vue": "^9.29.0",
    "happy-dom": "^15.7.4",
    "prettier": "^3.3.3",
    "sass": "^1.79.5",
    "typescript": "^5.6.3",
    "vite": "^5.4.10",
    "vitest": "^2.1.4",
    "vue-tsc": "^2.1.10"
  }
}
```

- [ ] **Step 2: 写 `dashboard/vite.config.ts`**

```ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
```

- [ ] **Step 3: 写 `dashboard/vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  test: { environment: 'happy-dom', globals: true },
})
```

- [ ] **Step 4: 写 `dashboard/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "jsx": "preserve",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["node", "vitest/globals"],
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src/**/*.ts", "src/**/*.vue", "src/**/*.d.ts"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 5: 写 `dashboard/tsconfig.node.json`**

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "skipLibCheck": true,
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

- [ ] **Step 6: 写 `dashboard/.eslintrc.cjs`**

```js
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:vue/vue3-recommended',
  ],
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    sourceType: 'module',
    ecmaVersion: 2022,
  },
  rules: {
    'vue/multi-word-component-names': 'off',
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
  },
}
```

- [ ] **Step 7: 写 `dashboard/.prettierrc`**

```json
{ "semi": false, "singleQuote": true, "trailingComma": "all", "printWidth": 100 }
```

- [ ] **Step 8: 写 `dashboard/.gitignore`**

```
node_modules
dist
.DS_Store
*.local
```

- [ ] **Step 9: 写 `dashboard/index.html`**

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>反馈台</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 10: 写 `dashboard/public/favicon.svg`**

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#3b82f6"/><text x="16" y="22" font-size="18" text-anchor="middle" fill="#fff" font-family="system-ui">反</text></svg>
```

- [ ] **Step 11: 安装依赖**

Run: `cd dashboard && npm install`
Expected: 安装成功，无 ERR

- [ ] **Step 12: Commit**

```bash
git add dashboard/.gitignore dashboard/.eslintrc.cjs dashboard/.prettierrc \
        dashboard/package.json dashboard/package-lock.json \
        dashboard/vite.config.ts dashboard/vitest.config.ts \
        dashboard/tsconfig.json dashboard/tsconfig.node.json \
        dashboard/index.html dashboard/public/favicon.svg
git commit -m "chore(dashboard): scaffold Vite + Vue 3 + TS project"
```

---

## Task 3：常量与样式基线

**Files:**
- Create: `dashboard/src/constants/labels.ts`
- Create: `dashboard/src/styles/global.scss`
- Create: `dashboard/src/styles/element-overrides.scss`

- [ ] **Step 1: 写 `dashboard/src/constants/labels.ts`**

> 必须与 `feedback_hub/config.py` 的 `L1_VALUES / L2_VALUES / SEVERITY_VALUES` 一致。

```ts
export const L1_VALUES = ['A.Bug', 'B.建议', 'C.咨询', 'D.情绪', 'E.无效', '待定'] as const
export type L1 = (typeof L1_VALUES)[number]

export const L2_VALUES = [
  '输入核心', '语音', '符号表情', '皮肤', '词库', '账号',
  '键盘交互', '安装更新', '性能', '权限隐私', '广告活动', '其他',
] as const
export type L2 = (typeof L2_VALUES)[number]

export const SEVERITY_VALUES = ['P0', 'P1', 'P2', 'P3'] as const
export type Severity = (typeof SEVERITY_VALUES)[number]

export const SEVERITY_COLOR: Record<Severity, { fg: string; bg: string }> = {
  P0: { fg: '#ef4444', bg: '#fee2e2' },
  P1: { fg: '#f97316', bg: '#ffedd5' },
  P2: { fg: '#3b82f6', bg: '#dbeafe' },
  P3: { fg: '#6b7280', bg: '#f3f4f6' },
}

export const L1_COLOR: Record<L1, { fg: string; bg: string }> = {
  'A.Bug':  { fg: '#ef4444', bg: '#fee2e2' },
  'B.建议': { fg: '#3b82f6', bg: '#dbeafe' },
  'C.咨询': { fg: '#6b7280', bg: '#f3f4f6' },
  'D.情绪': { fg: '#8b5cf6', bg: '#ede9fe' },
  'E.无效': { fg: '#9ca3af', bg: '#f3f4f6' },
  '待定':   { fg: '#d97706', bg: '#fef3c7' },
}
```

- [ ] **Step 2: 写 `dashboard/src/styles/global.scss`**

```scss
:root {
  --bg: #f7f8fa;
  --bg-card: #ffffff;
  --border: #e5e7eb;
  --text: #1f2937;
  --text-muted: #6b7280;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --success: #10b981;
  --danger: #ef4444;
  --warning: #f59e0b;
  --radius: 8px;
}

* { box-sizing: border-box; }
html, body, #app { height: 100%; margin: 0; padding: 0; }

body {
  font-family: system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: var(--text);
  background: var(--bg);
}

.font-mono { font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace; }

.card {
  background: var(--bg-card);
  border-radius: var(--radius);
  border: 1px solid var(--border);
  padding: 16px;
  transition: box-shadow 0.2s;
  &:hover { box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06); }
}

.page { padding: 24px; }
.page-title { font-size: 20px; font-weight: 600; margin: 0 0 16px; }
.muted { color: var(--text-muted); }
```

- [ ] **Step 3: 写 `dashboard/src/styles/element-overrides.scss`**

```scss
:root {
  --el-color-primary: #3b82f6;
  --el-color-primary-light-3: #60a5fa;
  --el-color-primary-light-5: #93c5fd;
  --el-color-primary-light-7: #bfdbfe;
  --el-color-primary-light-9: #dbeafe;
  --el-color-primary-dark-2: #2563eb;
  --el-border-radius-base: 8px;
}
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/constants dashboard/src/styles
git commit -m "feat(dashboard): add label constants and base styles"
```

---

## Task 4：工具函数 + 测试

**Files:**
- Create: `dashboard/src/utils/format.ts`
- Create: `dashboard/src/__tests__/format.test.ts`

- [ ] **Step 1: 写 `dashboard/src/__tests__/format.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { formatTs, formatTsShort, truncate, formatPercent } from '@/utils/format'

describe('formatTs', () => {
  it('formats unix ms to YYYY-MM-DD HH:mm', () => {
    const ms = new Date(2026, 4, 21, 14, 30, 0).getTime()
    expect(formatTs(ms)).toBe('2026-05-21 14:30')
  })
  it('returns empty string for null', () => {
    expect(formatTs(null)).toBe('')
  })
})

describe('formatTsShort', () => {
  it('formats unix ms to MM-DD HH:mm', () => {
    const ms = new Date(2026, 4, 21, 14, 30, 0).getTime()
    expect(formatTsShort(ms)).toBe('05-21 14:30')
  })
})

describe('truncate', () => {
  it('returns full text when shorter than limit', () => {
    expect(truncate('hello', 10)).toBe('hello')
  })
  it('truncates and appends ellipsis', () => {
    expect(truncate('1234567890abcdef', 10)).toBe('1234567890…')
  })
  it('handles null/undefined', () => {
    expect(truncate(null, 10)).toBe('')
    expect(truncate(undefined, 10)).toBe('')
  })
})

describe('formatPercent', () => {
  it('formats 0.476 to "47.6%"', () => {
    expect(formatPercent(0.476)).toBe('47.6%')
  })
  it('formats 0 to "0.0%"', () => {
    expect(formatPercent(0)).toBe('0.0%')
  })
  it('handles NaN', () => {
    expect(formatPercent(Number.NaN)).toBe('—')
  })
})
```

- [ ] **Step 2: 运行测试，确认 FAIL**

Run: `cd dashboard && npx vitest run src/__tests__/format.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 `dashboard/src/utils/format.ts`**

```ts
import dayjs from 'dayjs'

export function formatTs(ms: number | null | undefined): string {
  if (ms == null) return ''
  return dayjs(ms).format('YYYY-MM-DD HH:mm')
}

export function formatTsShort(ms: number | null | undefined): string {
  if (ms == null) return ''
  return dayjs(ms).format('MM-DD HH:mm')
}

export function truncate(s: string | null | undefined, max: number): string {
  if (!s) return ''
  if (s.length <= max) return s
  return s.slice(0, max) + '…'
}

export function formatPercent(ratio: number): string {
  if (Number.isNaN(ratio) || !Number.isFinite(ratio)) return '—'
  return `${(ratio * 100).toFixed(1)}%`
}
```

- [ ] **Step 4: 运行测试，确认 PASS**

Run: `cd dashboard && npx vitest run src/__tests__/format.test.ts`
Expected: PASS（8 条全绿）

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/utils dashboard/src/__tests__/format.test.ts
git commit -m "feat(dashboard): add format utilities with tests"
```

---

## Task 5：API 层 + 测试

**Files:**
- Create: `dashboard/src/api/http.ts`
- Create: `dashboard/src/api/feedback.ts`
- Create: `dashboard/src/__tests__/feedback-api.test.ts`

- [ ] **Step 1: 写 `dashboard/src/api/http.ts`**

```ts
import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'

export const http = axios.create({ baseURL: '', timeout: 30000 })

export interface ApiError {
  type: 'NOT_FOUND' | 'SERVER_ERROR' | 'NETWORK_ERROR' | 'BAD_REQUEST' | 'UNKNOWN'
  detail?: string
}

http.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError<{ detail?: string }>) => {
    let apiErr: ApiError
    if (!err.response) {
      apiErr = { type: 'NETWORK_ERROR', detail: err.message }
      ElMessage.error('网络错误，请检查后端服务')
    } else if (err.response.status === 404) {
      apiErr = { type: 'NOT_FOUND', detail: err.response.data?.detail }
    } else if (err.response.status === 400) {
      apiErr = { type: 'BAD_REQUEST', detail: err.response.data?.detail }
      ElMessage.error(`请求错误：${apiErr.detail ?? ''}`)
    } else if (err.response.status >= 500) {
      apiErr = { type: 'SERVER_ERROR', detail: err.response.data?.detail }
      ElMessage.error('服务异常，请稍后重试')
    } else {
      apiErr = { type: 'UNKNOWN', detail: err.message }
    }
    return Promise.reject(apiErr)
  },
)
```

- [ ] **Step 2: 写 `dashboard/src/api/feedback.ts`**

```ts
import { http } from './http'
import type { Severity } from '@/constants/labels'

export interface ConversationItem {
  conversation_id: string
  L1: string
  L2: string[]
  severity: Severity
  confidence: number
  reason: string | null
  msg_count: number
  first_ts_ms: number
  last_ts_ms: number
  user_vid: string | null
  appversion: string | null
  channel: string
  preview_text: string
}

export interface MessageItem {
  feedback_id: string
  msg_seq: number
  ts_ms: number
  text: string
  appversion: string | null
  platform: string | null
  L1: string | null
  L2: string[]
  severity: Severity | null
  confidence: number | null
  reason: string | null
  source: 'rule' | 'llm' | null
  rule_name: string | null
}

export interface ListResp { total: number; items: ConversationItem[] }
export interface DetailResp { conversation: ConversationItem; messages: MessageItem[] }
export interface DistributionResp {
  L1: Record<string, number>
  L2: Record<string, number>
  severity: Record<string, number>
}
export interface TrendBucket { bucket: string; counts: Record<string, number> }
export interface TrendResp { granularity: 'day' | 'hour'; buckets: TrendBucket[] }

export interface ListParams {
  from?: string
  to?: string
  L1?: string
  L2?: string
  severity?: string
  q?: string
  limit?: number
  offset?: number
}

function clean<T extends Record<string, unknown>>(obj: T): Partial<T> {
  const out: Partial<T> = {}
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined && v !== null && v !== '') {
      ;(out as Record<string, unknown>)[k] = v
    }
  }
  return out
}

export async function listConversations(params: ListParams): Promise<ListResp> {
  const r = await http.get<ListResp>('/api/conversations', { params: clean(params) })
  return r.data
}

export async function getConversation(id: string): Promise<DetailResp> {
  const r = await http.get<DetailResp>(`/api/conversations/${encodeURIComponent(id)}`)
  return r.data
}

export async function getDistribution(p: { from?: string; to?: string }): Promise<DistributionResp> {
  const r = await http.get<DistributionResp>('/api/stats/distribution', { params: clean(p) })
  return r.data
}

export async function getTrend(
  p: { from?: string; to?: string; granularity?: 'day' | 'hour' },
): Promise<TrendResp> {
  const r = await http.get<TrendResp>('/api/stats/trend', { params: clean(p) })
  return r.data
}

export function exportCsvUrl(params: ListParams): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(clean(params))) {
    qs.set(k, String(v))
  }
  return `/api/export.csv${qs.toString() ? '?' + qs.toString() : ''}`
}
```

- [ ] **Step 3: 写 `dashboard/src/__tests__/feedback-api.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/http', () => ({ http: { get: vi.fn() } }))

import { http } from '@/api/http'
import {
  listConversations, getConversation, getDistribution, getTrend, exportCsvUrl,
} from '@/api/feedback'

const mockedGet = vi.mocked(http.get)
beforeEach(() => { mockedGet.mockReset() })

describe('listConversations', () => {
  it('omits empty params', async () => {
    mockedGet.mockResolvedValue({ data: { total: 0, items: [] } })
    await listConversations({ L1: '', q: undefined, limit: 50 })
    expect(mockedGet).toHaveBeenCalledWith('/api/conversations', { params: { limit: 50 } })
  })
  it('passes through filled params', async () => {
    mockedGet.mockResolvedValue({ data: { total: 1, items: [] } })
    await listConversations({ L1: 'A.Bug', from: '2026-05-15', to: '2026-05-21' })
    expect(mockedGet).toHaveBeenCalledWith('/api/conversations', {
      params: { L1: 'A.Bug', from: '2026-05-15', to: '2026-05-21' },
    })
  })
})

describe('getConversation', () => {
  it('encodes id in path', async () => {
    mockedGet.mockResolvedValue({ data: { conversation: {} as never, messages: [] } })
    await getConversation('abc 123')
    expect(mockedGet).toHaveBeenCalledWith('/api/conversations/abc%20123')
  })
})

describe('getDistribution', () => {
  it('passes from/to', async () => {
    mockedGet.mockResolvedValue({ data: { L1: {}, L2: {}, severity: {} } })
    await getDistribution({ from: '2026-05-15', to: '2026-05-21' })
    expect(mockedGet).toHaveBeenCalledWith('/api/stats/distribution', {
      params: { from: '2026-05-15', to: '2026-05-21' },
    })
  })
})

describe('getTrend', () => {
  it('passes granularity', async () => {
    mockedGet.mockResolvedValue({ data: { granularity: 'day', buckets: [] } })
    await getTrend({ granularity: 'day' })
    expect(mockedGet).toHaveBeenCalledWith('/api/stats/trend', { params: { granularity: 'day' } })
  })
})

describe('exportCsvUrl', () => {
  it('builds url with query string', () => {
    const url = exportCsvUrl({ L1: 'A.Bug', from: '2026-05-15' })
    expect(url).toContain('/api/export.csv?')
    expect(url).toContain('L1=A.Bug')
    expect(url).toContain('from=2026-05-15')
  })
  it('returns plain path when no params', () => {
    expect(exportCsvUrl({})).toBe('/api/export.csv')
  })
})
```

- [ ] **Step 4: 运行测试，确认 PASS**

Run: `cd dashboard && npx vitest run src/__tests__/feedback-api.test.ts`
Expected: PASS（7 条全绿）

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/api dashboard/src/__tests__/feedback-api.test.ts
git commit -m "feat(dashboard): add api layer with axios interceptors and tests"
```

---

## Task 6：composable `useUrlQuery` + 测试

**Files:**
- Create: `dashboard/src/composables/useUrlQuery.ts`
- Create: `dashboard/src/__tests__/useUrlQuery.test.ts`

- [ ] **Step 1: 写 `dashboard/src/__tests__/useUrlQuery.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'
import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { useUrlQuery } from '@/composables/useUrlQuery'

function makeRouter(initialPath = '/list') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/list', component: { template: '<div />' } }],
  })
  router.push(initialPath)
  return router
}

const Probe = defineComponent({
  props: { defaults: { type: Object, required: true } },
  setup(props) {
    const state = useUrlQuery(props.defaults as Record<string, string>)
    return { state }
  },
  render() { return h('pre', JSON.stringify(this.state)) },
})

describe('useUrlQuery', () => {
  it('reads initial values from URL', async () => {
    const router = makeRouter('/list?L1=A.Bug&from=2026-05-15')
    await router.isReady()
    const w = mount(Probe, {
      props: { defaults: { L1: '', from: '' } },
      global: { plugins: [router] },
    })
    expect(w.vm.state.L1).toBe('A.Bug')
    expect(w.vm.state.from).toBe('2026-05-15')
  })

  it('applies defaults when URL has no params', async () => {
    const router = makeRouter('/list')
    await router.isReady()
    const w = mount(Probe, {
      props: { defaults: { L1: '', from: '2026-05-14' } },
      global: { plugins: [router] },
    })
    expect(w.vm.state.from).toBe('2026-05-14')
  })

  it('writing state updates URL query', async () => {
    const router = makeRouter('/list')
    await router.isReady()
    const w = mount(Probe, {
      props: { defaults: { L1: '' } },
      global: { plugins: [router] },
    })
    w.vm.state.L1 = 'B.建议'
    await nextTick()
    await nextTick()
    expect(router.currentRoute.value.query.L1).toBe('B.建议')
  })
})
```

- [ ] **Step 2: 运行测试，确认 FAIL**

Run: `cd dashboard && npx vitest run src/__tests__/useUrlQuery.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 `dashboard/src/composables/useUrlQuery.ts`**

```ts
import { reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export function useUrlQuery<T extends Record<string, string>>(defaults: T): T {
  const route = useRoute()
  const router = useRouter()

  const initial = {} as T
  for (const k of Object.keys(defaults) as (keyof T)[]) {
    const fromUrl = route.query[k as string]
    if (typeof fromUrl === 'string' && fromUrl !== '') {
      ;(initial as Record<string, string>)[k as string] = fromUrl
    } else {
      ;(initial as Record<string, string>)[k as string] = defaults[k]
    }
  }

  const state = reactive(initial) as T

  watch(
    () => ({ ...state }),
    (next) => {
      const query: Record<string, string> = {}
      for (const [k, v] of Object.entries(next)) {
        if (v !== '' && v != null) query[k] = v
      }
      router.replace({ query })
    },
    { deep: true },
  )

  return state
}
```

- [ ] **Step 4: 运行测试，确认 PASS**

Run: `cd dashboard && npx vitest run src/__tests__/useUrlQuery.test.ts`
Expected: PASS（3 条全绿）

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/composables/useUrlQuery.ts dashboard/src/__tests__/useUrlQuery.test.ts
git commit -m "feat(dashboard): add useUrlQuery composable with tests"
```

---

## Task 7：原子组件 SeverityTag / L1Tag / KpiCard + 测试

**Files:**
- Create: `dashboard/src/components/{SeverityTag.vue, L1Tag.vue, KpiCard.vue}`
- Create: `dashboard/src/__tests__/{SeverityTag,L1Tag,KpiCard}.test.ts`

- [ ] **Step 1: 写三份测试**

`dashboard/src/__tests__/SeverityTag.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SeverityTag from '@/components/SeverityTag.vue'

describe('SeverityTag', () => {
  it('renders severity text', () => {
    const w = mount(SeverityTag, { props: { value: 'P0' } })
    expect(w.text()).toBe('P0')
  })
  it('uses red color for P0', () => {
    const w = mount(SeverityTag, { props: { value: 'P0' } })
    expect(w.attributes('style')).toContain('#ef4444')
  })
  it('renders dash when value is null', () => {
    const w = mount(SeverityTag, { props: { value: null } })
    expect(w.text()).toBe('—')
  })
})
```

`dashboard/src/__tests__/L1Tag.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import L1Tag from '@/components/L1Tag.vue'

describe('L1Tag', () => {
  it('renders L1 text', () => {
    const w = mount(L1Tag, { props: { value: 'A.Bug' } })
    expect(w.text()).toBe('A.Bug')
  })
  it('uses red color for A.Bug', () => {
    const w = mount(L1Tag, { props: { value: 'A.Bug' } })
    expect(w.attributes('style')).toContain('#ef4444')
  })
  it('falls back to neutral for unknown L1', () => {
    const w = mount(L1Tag, { props: { value: 'Z.未知' } })
    expect(w.text()).toBe('Z.未知')
  })
})
```

`dashboard/src/__tests__/KpiCard.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import KpiCard from '@/components/KpiCard.vue'

describe('KpiCard', () => {
  it('renders title and value', () => {
    const w = mount(KpiCard, { props: { title: '总会话', value: '142' } })
    expect(w.text()).toContain('总会话')
    expect(w.text()).toContain('142')
  })
  it('shows tooltip text when provided', () => {
    const w = mount(KpiCard, {
      props: { title: '自动定级率', value: '78%', tooltip: '非待定占比' },
    })
    expect(w.html()).toContain('非待定占比')
  })
})
```

- [ ] **Step 2: 运行测试，确认 FAIL**

Run: `cd dashboard && npx vitest run src/__tests__/SeverityTag.test.ts src/__tests__/L1Tag.test.ts src/__tests__/KpiCard.test.ts`
Expected: FAIL（组件文件不存在）

- [ ] **Step 3: 写 `dashboard/src/components/SeverityTag.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { SEVERITY_COLOR, type Severity } from '@/constants/labels'

const props = defineProps<{ value: Severity | null | undefined }>()

const style = computed(() => {
  if (!props.value) return {}
  const c = SEVERITY_COLOR[props.value]
  return {
    color: c.fg, background: c.bg,
    padding: '2px 8px', borderRadius: '4px',
    fontSize: '12px', fontWeight: 600, display: 'inline-block',
  }
})
</script>

<template>
  <span v-if="value" :style="style">{{ value }}</span>
  <span v-else class="muted">—</span>
</template>
```

- [ ] **Step 4: 写 `dashboard/src/components/L1Tag.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { L1_COLOR } from '@/constants/labels'

const props = defineProps<{ value: string | null | undefined }>()
const NEUTRAL = { fg: '#6b7280', bg: '#f3f4f6' }

const style = computed(() => {
  if (!props.value) return {}
  const c = (L1_COLOR as Record<string, { fg: string; bg: string }>)[props.value] ?? NEUTRAL
  return {
    color: c.fg, background: c.bg,
    padding: '2px 8px', borderRadius: '4px',
    fontSize: '12px', fontWeight: 600, display: 'inline-block',
  }
})
</script>

<template>
  <span v-if="value" :style="style">{{ value }}</span>
  <span v-else class="muted">—</span>
</template>
```

- [ ] **Step 5: 写 `dashboard/src/components/KpiCard.vue`**

```vue
<script setup lang="ts">
defineProps<{ title: string; value: string | number; tooltip?: string }>()
</script>

<template>
  <div class="card kpi">
    <div class="kpi-title">
      {{ title }}
      <el-tooltip v-if="tooltip" :content="tooltip" placement="top">
        <span class="kpi-hint" :title="tooltip">ⓘ</span>
      </el-tooltip>
    </div>
    <div class="kpi-value font-mono">{{ value }}</div>
  </div>
</template>

<style scoped>
.kpi { padding: 16px 20px; height: 96px; display: flex; flex-direction: column; justify-content: center; }
.kpi-title { color: var(--text-muted); font-size: 13px; margin-bottom: 8px; }
.kpi-value { color: var(--text); font-size: 24px; font-weight: 700; }
.kpi-hint { color: var(--text-muted); cursor: help; margin-left: 4px; }
</style>
```

- [ ] **Step 6: 运行测试，确认 PASS**

Run: `cd dashboard && npx vitest run src/__tests__/SeverityTag.test.ts src/__tests__/L1Tag.test.ts src/__tests__/KpiCard.test.ts`
Expected: PASS（8 条全绿）

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/SeverityTag.vue dashboard/src/components/L1Tag.vue \
        dashboard/src/components/KpiCard.vue \
        dashboard/src/__tests__/SeverityTag.test.ts \
        dashboard/src/__tests__/L1Tag.test.ts \
        dashboard/src/__tests__/KpiCard.test.ts
git commit -m "feat(dashboard): add SeverityTag, L1Tag, KpiCard components"
```

---

## Task 8：图表组件（不写测试）

**Files:**
- Create: `dashboard/src/components/{ChartPie.vue, ChartBar.vue, ChartTrend.vue}`

- [ ] **Step 1: 写 `dashboard/src/components/ChartPie.vue`**

```vue
<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref, shallowRef } from 'vue'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([PieChart, TitleComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  data: Record<string, number>
  colorMap?: Record<string, string>
  height?: string
}>()

const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

function render() {
  if (!chart.value) return
  const items = Object.entries(props.data).map(([name, value]) => ({
    name, value,
    itemStyle: props.colorMap?.[name] ? { color: props.colorMap[name] } : undefined,
  }))
  chart.value.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{
      type: 'pie',
      radius: ['40%', '65%'],
      center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: '{b}\n{d}%' },
      data: items,
    }],
  })
}

function resize() { chart.value?.resize() }

onMounted(() => {
  if (el.value) {
    chart.value = echarts.init(el.value)
    render()
    window.addEventListener('resize', resize)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
})

watch(() => props.data, render, { deep: true })
</script>

<template>
  <div ref="el" :style="{ height: height ?? '280px', width: '100%' }" />
</template>
```

- [ ] **Step 2: 写 `dashboard/src/components/ChartBar.vue`**

```vue
<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref, shallowRef } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, GridComponent, TitleComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ data: Record<string, number>; topN?: number; height?: string }>()
const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

function render() {
  if (!chart.value) return
  const entries = Object.entries(props.data).sort((a, b) => b[1] - a[1])
  const top = props.topN ? entries.slice(0, props.topN) : entries
  const names = top.map(([k]) => k).reverse()
  const values = top.map(([, v]) => v).reverse()
  chart.value.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 16, top: 16, bottom: 24 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: names },
    series: [{ type: 'bar', data: values, itemStyle: { color: '#3b82f6' } }],
  })
}

function resize() { chart.value?.resize() }

onMounted(() => {
  if (el.value) {
    chart.value = echarts.init(el.value)
    render()
    window.addEventListener('resize', resize)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
})
watch(() => props.data, render, { deep: true })
</script>

<template>
  <div ref="el" :style="{ height: height ?? '280px', width: '100%' }" />
</template>
```

- [ ] **Step 3: 写 `dashboard/src/components/ChartTrend.vue`**

```vue
<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref, shallowRef, computed } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { TrendBucket } from '@/api/feedback'
import { L1_COLOR, L1_VALUES } from '@/constants/labels'

echarts.use([LineChart, GridComponent, TitleComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ buckets: TrendBucket[]; height?: string }>()
const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

const series = computed(() => {
  const l1Set = new Set<string>()
  for (const b of props.buckets) for (const k of Object.keys(b.counts)) l1Set.add(k)
  const l1List = (L1_VALUES as readonly string[]).filter((v) => l1Set.has(v)).concat(
    [...l1Set].filter((v) => !(L1_VALUES as readonly string[]).includes(v)),
  )
  return l1List.map((l1) => ({
    name: l1,
    type: 'line',
    stack: 'total',
    areaStyle: {},
    smooth: true,
    itemStyle: { color: (L1_COLOR as Record<string, { fg: string; bg: string }>)[l1]?.fg ?? '#6b7280' },
    data: props.buckets.map((b) => b.counts[l1] ?? 0),
  }))
})

function render() {
  if (!chart.value) return
  chart.value.setOption({
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, type: 'scroll' },
    grid: { left: 40, right: 24, top: 16, bottom: 48 },
    xAxis: { type: 'category', data: props.buckets.map((b) => b.bucket) },
    yAxis: { type: 'value' },
    series: series.value,
  }, true)
}

function resize() { chart.value?.resize() }

onMounted(() => {
  if (el.value) {
    chart.value = echarts.init(el.value)
    render()
    window.addEventListener('resize', resize)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
})
watch(() => props.buckets, render, { deep: true })
</script>

<template>
  <div ref="el" :style="{ height: height ?? '320px', width: '100%' }" />
</template>
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/ChartPie.vue dashboard/src/components/ChartBar.vue \
        dashboard/src/components/ChartTrend.vue
git commit -m "feat(dashboard): add ECharts wrapper components (Pie/Bar/Trend)"
```

---

## Task 9：复合组件 ConversationTable + FilterBar

**Files:**
- Create: `dashboard/src/components/{ConversationTable.vue, FilterBar.vue}`

- [ ] **Step 1: 写 `dashboard/src/components/ConversationTable.vue`**

```vue
<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { ConversationItem } from '@/api/feedback'
import L1Tag from './L1Tag.vue'
import SeverityTag from './SeverityTag.vue'
import { formatTs, truncate } from '@/utils/format'

defineProps<{ items: ConversationItem[]; loading?: boolean; emptyText?: string }>()

const router = useRouter()
function gotoDetail(row: ConversationItem) {
  router.push(`/feedback/${row.conversation_id}`)
}
</script>

<template>
  <el-table
    v-loading="loading"
    :data="items"
    style="width: 100%"
    :empty-text="emptyText ?? '暂无数据'"
    row-class-name="clickable-row"
    @row-click="gotoDetail"
  >
    <el-table-column label="时间" width="160">
      <template #default="{ row }">
        <span class="font-mono">{{ formatTs(row.last_ts_ms) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="反馈片段" min-width="320">
      <template #default="{ row }">{{ truncate(row.preview_text, 80) }}</template>
    </el-table-column>
    <el-table-column label="L1" width="100">
      <template #default="{ row }"><L1Tag :value="row.L1" /></template>
    </el-table-column>
    <el-table-column label="L2" width="200">
      <template #default="{ row }">
        <span v-if="row.L2.length === 0" class="muted">—</span>
        <span v-else>{{ row.L2.join(' / ') }}</span>
      </template>
    </el-table-column>
    <el-table-column label="severity" width="100">
      <template #default="{ row }"><SeverityTag :value="row.severity" /></template>
    </el-table-column>
    <el-table-column label="平台·版本" width="160">
      <template #default="{ row }">
        <span class="muted">{{ row.appversion ?? '—' }}</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
:deep(.clickable-row) { cursor: pointer; }
:deep(.clickable-row:hover) { background: #f7f8fa; }
</style>
```

- [ ] **Step 2: 写 `dashboard/src/components/FilterBar.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { L1_VALUES, L2_VALUES, SEVERITY_VALUES } from '@/constants/labels'

interface FilterState {
  q: string
  L1: string
  L2: string
  severity: string
  from: string
  to: string
}

const props = defineProps<{ modelValue: FilterState }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: FilterState): void
  (e: 'apply'): void
  (e: 'clear'): void
  (e: 'export'): void
}>()

const dateRange = computed<[string, string] | null>({
  get() {
    if (props.modelValue.from && props.modelValue.to) {
      return [props.modelValue.from, props.modelValue.to]
    }
    return null
  },
  set(v) {
    emit('update:modelValue', {
      ...props.modelValue, from: v?.[0] ?? '', to: v?.[1] ?? '',
    })
  },
})

function update<K extends keyof FilterState>(k: K, v: FilterState[K]) {
  emit('update:modelValue', { ...props.modelValue, [k]: v })
}
</script>

<template>
  <div class="filter-bar card">
    <el-input
      :model-value="modelValue.q"
      placeholder="关键词搜索"
      style="width: 200px"
      clearable
      @update:model-value="update('q', $event ?? '')"
    />
    <el-select
      :model-value="modelValue.L1"
      placeholder="L1"
      clearable
      style="width: 120px"
      @update:model-value="update('L1', $event ?? '')"
    >
      <el-option v-for="x in L1_VALUES" :key="x" :label="x" :value="x" />
    </el-select>
    <el-select
      :model-value="modelValue.L2"
      placeholder="L2"
      clearable
      filterable
      style="width: 160px"
      @update:model-value="update('L2', $event ?? '')"
    >
      <el-option v-for="x in L2_VALUES" :key="x" :label="x" :value="x" />
    </el-select>
    <el-select
      :model-value="modelValue.severity"
      placeholder="severity"
      clearable
      style="width: 120px"
      @update:model-value="update('severity', $event ?? '')"
    >
      <el-option v-for="x in SEVERITY_VALUES" :key="x" :label="x" :value="x" />
    </el-select>
    <el-date-picker
      v-model="dateRange"
      type="daterange"
      value-format="YYYY-MM-DD"
      start-placeholder="起"
      end-placeholder="止"
      style="width: 240px"
    />
    <div class="filter-actions">
      <el-button type="primary" @click="emit('apply')">筛选</el-button>
      <el-button @click="emit('clear')">清空</el-button>
      <el-button @click="emit('export')">导出 CSV</el-button>
    </div>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
</style>
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/ConversationTable.vue dashboard/src/components/FilterBar.vue
git commit -m "feat(dashboard): add ConversationTable and FilterBar components"
```

---

## Task 10：composable `useApiHealth`

**Files:**
- Create: `dashboard/src/composables/useApiHealth.ts`

- [ ] **Step 1: 写 `dashboard/src/composables/useApiHealth.ts`**

```ts
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { getDistribution } from '@/api/feedback'

export function useApiHealth(intervalMs = 30000) {
  const healthy = ref(true)
  let timer: ReturnType<typeof setInterval> | null = null

  async function check() {
    try {
      await getDistribution({})
      healthy.value = true
    } catch {
      healthy.value = false
    }
  }

  onMounted(() => {
    void check()
    timer = setInterval(check, intervalMs)
  })

  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
  })

  return { healthy }
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/composables/useApiHealth.ts
git commit -m "feat(dashboard): add useApiHealth composable for status indicator"
```

---

## Task 11：路由 + main.ts + App.vue + 页面占位

**Files:**
- Create: `dashboard/src/router/index.ts`
- Create: `dashboard/src/main.ts`
- Create: `dashboard/src/App.vue`
- Create: `dashboard/src/views/{NotFound.vue, Overview.vue, List.vue, Detail.vue}`（页面 part2 实装，本任务先放占位）

- [ ] **Step 1: 写 `dashboard/src/views/NotFound.vue`**

```vue
<script setup lang="ts">
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="not-found">
    <h1>404</h1>
    <p class="muted">页面不存在</p>
    <el-button type="primary" @click="router.push('/')">返回首页</el-button>
  </div>
</template>

<style scoped>
.not-found { text-align: center; padding: 80px 24px; }
.not-found h1 { font-size: 64px; margin: 0 0 8px; color: var(--text-muted); }
.not-found p { margin: 0 0 24px; }
</style>
```

- [ ] **Step 2: 写 3 个页面占位**

`dashboard/src/views/Overview.vue`:

```vue
<template>
  <div class="page">
    <h1 class="page-title">概览</h1>
    <div class="card">概览页占位（part2 实装）</div>
  </div>
</template>
```

`dashboard/src/views/List.vue`:

```vue
<template>
  <div class="page">
    <h1 class="page-title">反馈列表</h1>
    <div class="card">列表页占位（part2 实装）</div>
  </div>
</template>
```

`dashboard/src/views/Detail.vue`:

```vue
<template>
  <div class="page">
    <h1 class="page-title">反馈详情</h1>
    <div class="card">详情页占位（part2 实装）</div>
  </div>
</template>
```

- [ ] **Step 3: 写 `dashboard/src/router/index.ts`**

```ts
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('@/views/Overview.vue') },
  { path: '/list', name: 'list', component: () => import('@/views/List.vue') },
  {
    path: '/feedback/:id',
    name: 'detail',
    component: () => import('@/views/Detail.vue'),
    props: true,
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFound.vue'),
  },
]

export const router = createRouter({ history: createWebHistory(), routes })
```

- [ ] **Step 4: 写 `dashboard/src/main.ts`**

```ts
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import { router } from './router'
import App from './App.vue'
import './styles/global.scss'
import './styles/element-overrides.scss'

createApp(App).use(router).use(ElementPlus).mount('#app')
```

- [ ] **Step 5: 写 `dashboard/src/App.vue`**

```vue
<script setup lang="ts">
import { useApiHealth } from '@/composables/useApiHealth'
const { healthy } = useApiHealth()
</script>

<template>
  <div class="layout">
    <header class="layout-header">
      <div class="layout-brand">反馈台</div>
      <div class="layout-status">
        <span class="status-dot" :class="{ down: !healthy }"></span>
        <span class="status-text muted">{{ healthy ? '正常' : '异常' }}</span>
      </div>
    </header>
    <div class="layout-body">
      <aside class="layout-sider">
        <nav class="nav">
          <router-link to="/" class="nav-item">概览</router-link>
          <router-link to="/list" class="nav-item">反馈列表</router-link>
        </nav>
      </aside>
      <main class="layout-main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout { display: flex; flex-direction: column; height: 100vh; }
.layout-header {
  height: 56px; background: #ffffff;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 24px;
}
.layout-brand { font-size: 16px; font-weight: 600; }
.layout-status { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.status-dot.down { background: var(--danger); }
.layout-body { flex: 1; display: flex; min-height: 0; }
.layout-sider {
  width: 200px; background: #ffffff;
  border-right: 1px solid var(--border); padding: 16px 0;
}
.nav { display: flex; flex-direction: column; }
.nav-item {
  display: block; padding: 10px 24px;
  color: var(--text); text-decoration: none; font-size: 14px;
  border-left: 3px solid transparent;
}
.nav-item:hover { background: var(--bg); }
.nav-item.router-link-active {
  background: var(--bg); color: var(--primary);
  border-left-color: var(--primary); font-weight: 600;
}
.layout-main { flex: 1; overflow: auto; }
</style>
```

- [ ] **Step 6: TS 编译检查**

Run: `cd dashboard && npx vue-tsc --noEmit`
Expected: 无错

- [ ] **Step 7: 跑全部单测**

Run: `cd dashboard && npm test`
Expected: PASS（4 个测试文件，约 21 条用例）

- [ ] **Step 8: dev 端到端冒烟**

另开终端跑后端：
```bash
cd /Users/charvel/Desktop/用户反馈_2026_0519
uvicorn feedback_hub.api:app --host 0.0.0.0 --port 8000
```

Run: `cd dashboard && npm run dev`
打开 `http://localhost:5173`，确认：
- 顶栏「反馈台」+ 状态灯绿色
- 左导航 2 项可点击
- 三个页面占位卡片可见
- 浏览器 Console 无报错

- [ ] **Step 9: Commit**

```bash
git add dashboard/src/router dashboard/src/main.ts dashboard/src/App.vue dashboard/src/views
git commit -m "feat(dashboard): add router, main entry and global layout (page placeholders)"
```

---

## Self-Review

- [x] **Spec coverage**：本 Part 覆盖 spec §3.2 全局布局、§5.1-5.5 数据契约、§6 技术栈、§7 视觉规范、§8 后端 CORS。页面线框（§4）与页面 AC（§10.1）由 part2 覆盖。
- [x] **No placeholders**：每步都有完整代码或可执行命令，无 TBD/TODO。
- [x] **Type consistency**：`ConversationItem` / `MessageItem` / `ListParams` / `FilterState` 在所有引用处签名一致；`SeverityTag.value` 与 `Severity | null` 类型对齐；`L1Tag.value` 接受任意 string 以容错。
- [x] **TDD 纪律**：format / feedback-api / useUrlQuery / 三个原子组件均"先红 → 实现 → 转绿"。
- [x] **Frequent commits**：11 个 Task，每 Task 一个 commit。

---

## Execution Handoff

**Plan Part 1 saved to `docs/superpowers/plans/2026-05-21-feedback-dashboard.md`. Part 2 (业务页面 + 验收) saved to `docs/superpowers/plans/2026-05-21-feedback-dashboard-part2.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — 每个 Task 派发一个 fresh 子 agent，Task 间 review；适合并行/解耦。
**2. Inline Execution** — 在当前会话连续执行，每 2-3 个 Task 一个 checkpoint。

回答你想走哪条线，我就开 craft 模式开始执行。
