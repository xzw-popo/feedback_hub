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
python3 -m uvicorn feedback_hub.api:app --host 127.0.0.1 --port 8000
```

> 也可直接用 `uvicorn feedback_hub.api:app ...`，前提是 `uvicorn` 已在 PATH。

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

## Lint

```bash
npm run lint       # 0 error / 0 warning（max-warnings 0）
```

ESLint 9 + `eslint-plugin-vue` + `@typescript-eslint`。

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
