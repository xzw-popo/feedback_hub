# 微博舆情模块设计文档

> 日期：2026-06-23
> 状态：待评审
> 范围：在现有 `feedback_hub` 和 `dashboard` 旁新增独立的微博舆情模块，用于定时采集微博公开反馈、搜索微博内容、分析豆包/微信/对比舆论，并生成舆情报告。

## 1. 背景

当前系统已经支持从 APP 侧拉取用户反馈，并在前端提供搜索、筛选、详情和搜索报告能力。仓库中 `weibo_crawl_test/` 已验证可以通过关键词、时间范围、排序和页数拉取微博搜索结果，并将标准化结果写入 JSON/JSONL 或本地 SQLite。

微博反馈与 APP 内反馈的使用场景不同：

- APP 反馈更偏产品内问题、用户求助和稳定性线索。
- 微博反馈更偏公开舆论、品牌感知、竞品比较和传播风险。
- 微博中需要特别关注豆包相关讨论，以及微信与豆包被放在一起比较时的用户倾向。

因此第一阶段不把微博数据强行并入 APP 反馈池，而是建设一个独立的“微博舆情”模块。该模块复用现有前端工程、后端 API 风格和报告生成经验，但保留独立的数据模型、标签体系和页面入口。

## 2. 目标与非目标

### 2.1 目标

- 后台定时按关键词组采集微博公开内容。
- 微博内容独立入库，保留原文、作者、发布时间、原文链接、图片、互动数、命中关键词和原始载荷。
- 对微博内容生成面向舆情分析的标签，包括品牌焦点、情绪立场、话题类型、帖子类型和风险级别。
- 前端新增一级模块“微博舆情”，包含概览、列表和报告页面。
- 用户可以在微博列表中按时间、关键词、品牌焦点、情绪立场、话题、风险级别筛选和搜索。
- 用户可以生成微博舆情报告，重点回答豆包相关、微信相关、微信与豆包对比相关的问题。
- 模块第一版与现有 APP 反馈模块保持边界清晰，后续再评估统一搜索或统一报告。

### 2.2 非目标

- 不在第一版把微博帖子转换成现有 `feedback / conversation_label / message_label` 模型。
- 不在第一版把 APP 反馈和微博反馈混合展示在同一个列表页。
- 不在第一版由前端手动触发实时抓取。
- 不在第一版实现复杂任务队列、分布式 worker 或多人权限管理。
- 不在第一版对微博评论链、转发链和用户画像做深度社交网络分析。
- 不在第一版承诺微博数据全量覆盖；采集结果受关键词、时间窗口、登录 cookie 和微博反爬影响。

## 3. 核心原则

1. **独立建模**：微博是公开舆情源，不是 APP 会话，先使用独立表和独立标签体系。
2. **后台采集**：前端展示最近采集状态和数据结果，不直接承担采集控制台职责。
3. **舆论优先**：除基础搜索外，页面必须突出豆包、微信、对比讨论、情绪和风险。
4. **原文可追溯**：每条微博结果必须能跳转原文，并保留抓取时的原始载荷。
5. **可渐进融合**：API 和字段命名保留 `source`、`brand_focus` 等公共概念，为后续统一搜索留接口空间。

## 4. 用户流程

### 4.1 每日查看微博舆情

1. 用户打开 Dashboard 左侧导航中的“微博舆情”。
2. 系统展示近 7 天概览，包括总采集量、豆包声量、微信声量、对比讨论声量、负向占比、高风险条数和最近采集时间。
3. 用户查看声量趋势、品牌焦点分布、情绪分布、热门话题和高风险微博。
4. 用户点击某个指标或图表进入微博列表，并自动带上对应筛选条件。

### 4.2 搜索微博反馈

1. 用户进入“微博列表”。
2. 用户输入关键词，选择时间范围、品牌焦点、情绪、话题或风险级别。
3. 系统返回微博帖子列表。
4. 用户可以查看正文、作者、发布时间、互动数、命中关键词、标签和原文链接。
5. 用户点击结果进入详情或打开微博原文。

### 4.3 生成舆情报告

1. 用户进入“舆情报告”。
2. 用户选择时间范围、分析对象和报告类型。
3. 可选报告类型：
   - 豆包相关舆情
   - 微信相关舆情
   - 微信与豆包对比舆情
   - 自定义关键词舆情
4. 系统基于当前筛选快照创建报告任务。
5. 报告完成后展示 Markdown 报告，并支持复制。

## 5. 信息架构

新增一级导航项：`微博舆情`。

### 5.1 路由

| 路由 | 页面 | 说明 |
|---|---|---|
| `/weibo` | 舆情概览 | 微博声量、情绪、话题和风险概览 |
| `/weibo/list` | 微博列表 | 搜索、筛选、查看微博帖子 |
| `/weibo/reports` | 舆情报告 | 创建和查看微博舆情报告 |
| `/weibo/posts/:id` | 微博详情 | 可选，第一版可用抽屉或独立路由 |

### 5.2 舆情概览

概览页默认展示近 7 天，并支持切换近 7 天、近 14 天、近 30 天和自定义时间范围。

关键区块：

- KPI：总微博数、豆包相关、微信相关、对比讨论、负向占比、高风险条数、最近采集时间。
- 趋势图：按天展示总量、豆包、微信、对比讨论声量。
- 情绪分布：正向、中性、负向、混合。
- 品牌焦点分布：微信、豆包、对比、其他。
- 热门话题：输入体验、AI 能力、隐私安全、广告打扰、迁移意愿、功能对比、品牌认知、故障问题等。
- 高风险微博：展示最近高风险或值得关注的帖子。

### 5.3 微博列表

筛选项：

- 关键词搜索。
- 时间范围。
- 品牌焦点：全部、微信、豆包、微信与豆包对比、其他。
- 情绪立场：全部、正向、中性、负向、混合。
- 话题类型。
- 帖子类型。
- 风险级别。
- 命中关键词组。

列表字段：

- 发布时间。
- 作者昵称。
- 正文摘要。
- 品牌焦点。
- 情绪立场。
- 话题标签。
- 风险级别。
- 互动数：转发、评论、点赞。
- 命中关键词。
- 原文链接。

URL query 需要同步筛选条件，便于分享和报告复现。

### 5.4 舆情报告

报告列表展示：

- 标题。
- 状态：pending、running、succeeded、failed。
- 报告类型。
- 时间范围。
- 样本数。
- 创建时间。
- 操作：查看、重试、复制 Markdown。

报告正文建议包含：

- 执行摘要。
- 数据范围与样本说明。
- 声量趋势。
- 豆包相关舆论。
- 微信相关舆论。
- 微信与豆包对比舆论。
- 主要负向点。
- 主要正向点。
- 代表性微博。
- 产品、运营或公关建议。
- 局限性说明。

## 6. 数据模型

### 6.1 `weibo_post`

```sql
CREATE TABLE IF NOT EXISTS weibo_post (
    id                  TEXT PRIMARY KEY,
    weibo_id            TEXT NOT NULL UNIQUE,
    source              TEXT NOT NULL DEFAULT 'weibo',
    url                 TEXT NOT NULL,
    author_id           TEXT,
    author_name         TEXT,
    author_verified     INTEGER NOT NULL DEFAULT 0,
    created_at_raw      TEXT,
    created_at_ms       INTEGER,
    text                TEXT NOT NULL,
    pic_urls_json       TEXT,
    reposts_count       INTEGER,
    comments_count      INTEGER,
    attitudes_count     INTEGER,
    first_seen_at       INTEGER NOT NULL,
    last_seen_at        INTEGER NOT NULL,
    raw_json            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weibo_post_created ON weibo_post(created_at_ms);
CREATE INDEX IF NOT EXISTS idx_weibo_post_seen ON weibo_post(last_seen_at);
```

说明：

- `id` 使用内部 ID，`weibo_id` 保留微博原始 ID。
- `created_at_raw` 保存微博原始时间字符串，`created_at_ms` 尽力解析，解析失败允许为空。
- `raw_json` 保存抓取标准化后的完整对象，便于后续补字段。

### 6.2 `weibo_hit`

```sql
CREATE TABLE IF NOT EXISTS weibo_hit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    weibo_id        TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    query_name      TEXT,
    searched_at     INTEGER NOT NULL,
    search_rank     INTEGER NOT NULL,
    source_mode     TEXT NOT NULL,
    FOREIGN KEY (weibo_id) REFERENCES weibo_post(weibo_id)
);
CREATE INDEX IF NOT EXISTS idx_weibo_hit_keyword ON weibo_hit(keyword, searched_at);
```

说明：

- 一条微博可能被多个关键词命中，因此命中记录独立保存。
- `query_name` 用于区分关键词组，如“豆包输入法”“微信输入法”“微信输入法 豆包”。

### 6.3 `weibo_label`

```sql
CREATE TABLE IF NOT EXISTS weibo_label (
    weibo_id        TEXT PRIMARY KEY,
    brand_focus     TEXT NOT NULL,
    sentiment       TEXT NOT NULL,
    topics_json     TEXT NOT NULL,
    post_type       TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    confidence      REAL,
    reason          TEXT,
    label_source    TEXT NOT NULL,
    labeled_at      INTEGER NOT NULL,
    FOREIGN KEY (weibo_id) REFERENCES weibo_post(weibo_id)
);
CREATE INDEX IF NOT EXISTS idx_weibo_label_brand ON weibo_label(brand_focus);
CREATE INDEX IF NOT EXISTS idx_weibo_label_sentiment ON weibo_label(sentiment);
CREATE INDEX IF NOT EXISTS idx_weibo_label_risk ON weibo_label(risk_level);
```

枚举建议：

- `brand_focus`: `wechat`、`doubao`、`comparison`、`other`。
- `sentiment`: `positive`、`neutral`、`negative`、`mixed`。
- `topics_json`: 可多选，第一版包括 `input_experience`、`ai_capability`、`privacy`、`ads`、`migration_intent`、`feature_comparison`、`brand_perception`、`bug`、`other`。
- `post_type`: `complaint`、`help`、`recommendation`、`review`、`reshare`、`news_discussion`、`other`。
- `risk_level`: `normal`、`watch`、`high`。
- `label_source`: `rule`、`llm`、`manual`。

### 6.4 `weibo_crawl_run`

```sql
CREATE TABLE IF NOT EXISTS weibo_crawl_run (
    id                  TEXT PRIMARY KEY,
    status              TEXT NOT NULL,
    config_json         TEXT NOT NULL,
    started_at          INTEGER NOT NULL,
    finished_at         INTEGER,
    total_seen          INTEGER NOT NULL DEFAULT 0,
    inserted_posts      INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_weibo_crawl_run_started ON weibo_crawl_run(started_at);
```

该表用于前端展示最近采集状态，也用于排查 cookie 失效、反爬或解析失败。

### 6.5 `weibo_report_job`

微博报告任务可参考现有搜索报告 MVP 的 `search_report_job` 设计，但使用微博专属快照字段。

```sql
CREATE TABLE IF NOT EXISTS weibo_report_job (
    id                  TEXT PRIMARY KEY,
    status              TEXT NOT NULL,
    title               TEXT NOT NULL,
    report_type         TEXT NOT NULL,
    filters_json        TEXT NOT NULL,
    weibo_ids_json      TEXT NOT NULL,
    sample_count        INTEGER NOT NULL DEFAULT 0,
    result_markdown     TEXT,
    error_message       TEXT,
    created_at          INTEGER NOT NULL,
    started_at          INTEGER,
    finished_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_weibo_report_created ON weibo_report_job(created_at);
CREATE INDEX IF NOT EXISTS idx_weibo_report_status ON weibo_report_job(status, created_at);
```

## 7. 采集与打标流程

### 7.1 关键词配置

第一版配置文件可以从 `weibo_crawl_test/queries.example.json` 演进为正式配置，例如：

```json
{
  "queries": [
    {"name": "微信输入法", "keyword": "微信输入法", "source": "pc", "sort": "time"},
    {"name": "微信键盘", "keyword": "微信键盘", "source": "pc", "sort": "time"},
    {"name": "豆包输入法", "keyword": "豆包输入法", "source": "pc", "sort": "time"},
    {"name": "微信输入法 豆包", "keyword": "微信输入法 豆包", "source": "pc", "sort": "time"},
    {"name": "微信键盘 豆包", "keyword": "微信键盘 豆包", "source": "pc", "sort": "time"}
  ]
}
```

### 7.2 定时任务

建议新增正式模块 `feedback_hub/weibo/`，将 `weibo_crawl_test/crawl_weibo.py` 中稳定的抓取、解析、去重逻辑迁入。

定时任务流程：

1. 读取关键词配置、时间窗口和 cookie。
2. 为本次采集创建 `weibo_crawl_run`。
3. 按关键词调用微博搜索。
4. 解析微博卡片，标准化字段。
5. Upsert `weibo_post`。
6. Insert `weibo_hit`。
7. 对新增或更新内容执行规则打标，必要时执行 LLM 打标。
8. 更新 `weibo_crawl_run` 状态和统计。

调度方式：

- 本地/内网第一版可继续使用 cron 或 launchd。
- 命令入口建议为 `python3 -m feedback_hub.weibo.crawl --config ...`。
- Cookie 从环境变量或本地配置读取，不在前端展示完整 cookie。

### 7.3 打标策略

第一版建议规则优先、LLM 补充。

规则示例：

- 文本同时出现微信与豆包，或命中对比关键词，`brand_focus = comparison`。
- 文本只明显出现豆包，`brand_focus = doubao`。
- 文本只明显出现微信输入法、微信键盘，`brand_focus = wechat`。
- 出现“难用、崩、垃圾、烦、广告、隐私、偷听”等负向词，`sentiment` 倾向负向，并根据强度提升 `risk_level`。
- 出现“好用、流畅、推荐、准确、方便”等正向词，`sentiment` 倾向正向。
- 出现“比、相比、换成、不如、吊打、替代”等词并涉及两个品牌，识别为对比讨论。

LLM 打标用于处理规则不确定样本，输出固定 JSON，字段与 `weibo_label` 对齐。

## 8. 后端 API

### 8.1 概览

`GET /api/weibo/stats?from=2026-06-01&to=2026-06-23`

响应包含：

- `total_posts`
- `brand_focus_counts`
- `sentiment_counts`
- `topic_counts`
- `risk_counts`
- `trend`
- `latest_crawl_run`
- `high_risk_posts`

### 8.2 列表

`GET /api/weibo/posts`

查询参数：

- `from`
- `to`
- `q`
- `brand_focus`
- `sentiment`
- `topic`
- `post_type`
- `risk_level`
- `keyword`
- `limit`
- `offset`

响应：

```json
{
  "total": 120,
  "items": [
    {
      "id": "wb_123",
      "weibo_id": "123",
      "url": "https://weibo.com/...",
      "author_name": "用户昵称",
      "created_at_ms": 1782180000000,
      "text": "微博正文",
      "reposts_count": 1,
      "comments_count": 5,
      "attitudes_count": 20,
      "keywords": ["微信输入法 豆包"],
      "brand_focus": "comparison",
      "sentiment": "negative",
      "topics": ["feature_comparison"],
      "post_type": "review",
      "risk_level": "watch",
      "reason": "同时比较微信和豆包的输入体验"
    }
  ]
}
```

### 8.3 详情

`GET /api/weibo/posts/{id}`

返回单条微博完整字段、命中历史、标签信息和原始 JSON。

### 8.4 采集状态

`GET /api/weibo/crawl-runs?limit=20`

用于页面展示最近采集时间、成功/失败状态和错误摘要。

### 8.5 报告

- `POST /api/weibo/reports`
- `GET /api/weibo/reports?limit=20`
- `GET /api/weibo/reports/{id}`
- `POST /api/weibo/reports/{id}/retry`

创建报告时后端保存 `weibo_ids` 快照。报告生成和重试不重新执行搜索。

## 9. 报告生成口径

报告必须在开头说明：

- 数据来源为微博公开搜索结果。
- 数据范围由关键词、时间窗口和微博返回结果决定。
- 结论基于当前样本，不代表全网绝对声量。

报告任务执行步骤：

1. 根据筛选条件查询 `weibo_post + weibo_label + weibo_hit`。
2. 保存 `weibo_ids` 快照。
3. 按报告类型筛选样本：
   - 豆包相关：`brand_focus = doubao` 或 `comparison` 中提及豆包。
   - 微信相关：`brand_focus = wechat` 或 `comparison` 中提及微信。
   - 对比舆情：`brand_focus = comparison`。
4. 对样本排序：
   - 高风险优先。
   - 互动数高优先。
   - 发布时间近优先。
5. 截断进入 LLM 的样本，默认最多 120 条，每条正文最多 600 字。
6. 生成客观统计和代表微博。
7. 调用 LLM 生成 Markdown 报告。
8. 保存任务状态和报告正文。

## 10. 前端设计约束

- 保持当前 Dashboard 的工作台风格，使用 Element Plus、ECharts、Vue Router 和现有 API 封装方式。
- 新页面应偏运营分析工具，不做营销式视觉。
- 微博结果列表比 APP 反馈列表多展示社媒元数据，但保持扫描效率。
- 标签文案在前端展示为中文：
  - `wechat` -> 微信
  - `doubao` -> 豆包
  - `comparison` -> 对比
  - `negative` -> 负向
  - `watch` -> 值得关注
- 报告页复用现有 Markdown 渲染工具。
- 所有筛选状态同步到 URL query。

## 11. 测试与验收

### 11.1 后端测试

- 微博解析函数测试：PC HTML 样本能解析出微博 ID、作者、正文、链接和图片。
- Upsert 测试：重复微博只更新 `last_seen_at`，命中历史保留。
- 标签规则测试：微信、豆包、对比、负向和风险规则覆盖核心样例。
- API 测试：列表筛选、分页、概览统计、报告任务创建和重试。
- 采集失败测试：HTTP 432、超时、非预期 HTML 时记录失败 run。

### 11.2 前端测试

- API adapter 单测。
- 路由测试：`/weibo`、`/weibo/list`、`/weibo/reports` 可访问。
- 筛选 URL 同步测试。
- 关键组件渲染测试：微博 KPI、微博列表、报告列表。

### 11.3 手动验收

- 定时任务跑完后，概览页最近采集时间更新。
- 微博列表可按“对比”筛选出微信与豆包同现内容。
- 搜索“豆包”能看到豆包相关微博。
- 报告生成后包含豆包、微信、对比舆情分析和代表微博。
- 原文链接可打开微博原文。

## 12. 迭代计划

### MVP

- 正式微博数据表。
- 定时采集命令。
- 规则打标。
- `/api/weibo/stats` 和 `/api/weibo/posts`。
- 前端舆情概览和微博列表。

### 第二阶段

- 微博舆情报告。
- LLM 辅助打标。
- 采集状态页面或组件。
- 高风险内容提醒。

### 后续可能方向

- 与 APP 反馈统一搜索。
- 统一报告中合并 APP 反馈和微博舆情。
- 人工修正微博标签。
- 评论和转发链分析。
- 更细的竞品词库和舆情预警规则。
