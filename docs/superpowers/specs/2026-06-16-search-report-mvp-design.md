# 搜索报告 MVP 设计文档

> 日期：2026-06-16
> 状态：待确认

## 1. 背景

当前反馈平台已经具备反馈入库、会话聚合、关键词搜索、AI 智能搜索和 AI 精筛能力。但现阶段 LLM 打标链路尚未稳定跑通，既有标签主要来自规则打标，可信度不足，不能作为搜索报告和主动洞察的主要依据。

搜索报告 MVP 的目标是在不依赖标签质量的前提下，基于一次已经发生的搜索结果，生成一份可追溯、可复制、可辅助产品判断的结构化报告。

## 2. 目标与非目标

### 2.1 目标

- 用户在完成 AI 搜索或关键词搜索后，可以基于当前结果生成报告。
- 报告任务异步执行，允许耗时数分钟，用户可以离开当前搜索状态继续使用系统。
- 报告绑定创建时的 `conversation_ids` 快照，不重新执行搜索。
- 报告主要基于原始反馈文本、搜索意图、AI 精筛分数和客观元数据生成。
- 报告内容明确标注为“基于本次搜索结果快照的分析”，避免被误读为全量事实。
- 报告可查看、可重试、可复制 Markdown。

### 2.2 非目标

- 不在本期实现全局主动洞察、日报、周报或跨时间窗口异常发现。
- 不在本期依赖 LLM 标签质量完成趋势分析。
- 不在本期引入独立队列、独立 worker 或复杂任务调度系统。
- 不在本期实现多人协作、评论、报告分享权限。
- 不在本期实现报告编辑器，仅提供 Markdown 渲染和复制。

## 3. 核心原则

1. **快照优先**：报告创建时保存命中的 `conversation_ids`。后续生成、重试、查看都以该快照为准。
2. **标签弱依赖**：`L1 / L2 / severity` 可作为弱参考和附录统计，不作为报告结论的唯一依据。
3. **证据可追溯**：关键发现必须尽量绑定代表反馈或会话，报告不输出无法追踪来源的判断。
4. **异步可离开**：几分钟级任务不能阻塞列表页，前端必须提供任务状态和报告找回入口。
5. **MVP 简洁**：先使用后端后台任务实现，部署风险暴露后再升级任务队列。

## 4. 用户流程

### 4.1 创建报告

1. 用户在反馈列表页使用 AI 搜索或关键词搜索。
2. 系统展示搜索结果，用户可选择执行 AI 精筛。
3. 用户点击“生成报告”。
4. 前端展示确认面板：
   - 搜索问题或关键词条件摘要
   - 当前命中结果数
   - 将分析的样本上限
   - 是否包含 AI 精筛分数
   - 提示报告可能需要几分钟
5. 用户确认后，前端提交当前结果的 `conversation_ids` 快照。
6. 后端创建报告任务并返回 `job_id`。
7. 前端显示任务状态入口，用户可继续浏览或修改筛选条件。

### 4.2 查看报告

1. 用户打开报告面板。
2. 面板展示最近报告任务：
   - 标题
   - 状态
   - 创建时间
   - 样本数
   - 搜索范围摘要
   - 操作：查看、重试
3. 报告完成后，用户打开详情。
4. 系统渲染 Markdown 报告，并提供复制 Markdown。

### 4.3 重试报告

失败任务可以重试。重试时继续使用原任务保存的 `conversation_ids` 快照、搜索 query、筛选条件和 AI 精筛分数，不重新搜索。

## 5. 数据模型

新增表：`search_report_job`。

```sql
CREATE TABLE IF NOT EXISTS search_report_job (
    id                    TEXT PRIMARY KEY,
    status                TEXT NOT NULL,
    title                 TEXT NOT NULL,
    query                 TEXT,
    search_type           TEXT NOT NULL,
    filters_json          TEXT,
    search_payload_json   TEXT,
    conversation_ids_json TEXT NOT NULL,
    ai_scores_json        TEXT,
    sample_count          INTEGER NOT NULL DEFAULT 0,
    result_markdown       TEXT,
    error_message         TEXT,
    created_at            INTEGER NOT NULL,
    started_at            INTEGER,
    finished_at           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_report_job_created ON search_report_job(created_at);
CREATE INDEX IF NOT EXISTS idx_report_job_status ON search_report_job(status, created_at);
```

字段说明：

- `id`：任务 ID，建议使用 UUID 或短 UUID。
- `status`：`pending / running / succeeded / failed`。
- `title`：报告标题，默认由搜索 query 或关键词条件生成。
- `query`：AI 搜索原始问题；关键词搜索可为空或保存可读摘要。
- `search_type`：`smart / keyword / mixed`。
- `filters_json`：创建任务时的元数据筛选快照。
- `search_payload_json`：创建任务时的搜索请求快照，用于展示和审计。
- `conversation_ids_json`：创建任务时命中的会话 ID 数组，是报告生成的事实边界。
- `ai_scores_json`：可选，保存 AI 精筛分数和理由。
- `sample_count`：本次实际进入 LLM 报告生成的样本数。
- `result_markdown`：报告正文。
- `error_message`：失败原因，面向用户展示时需要做简化。

## 6. 后端 API

### 6.1 创建报告任务

`POST /api/search-reports`

请求：

```json
{
  "title": "语音输入快捷键相关反馈分析",
  "query": "用户对语音输入快捷键的意见",
  "search_type": "smart",
  "filters": {
    "from": "2026-06-01",
    "to": "2026-06-16",
    "platform": "android"
  },
  "search_payload": {
    "debug": {
      "regex_patterns": ["语音|听写", "快捷键|热键"]
    }
  },
  "conversation_ids": ["conv_001", "conv_002"],
  "ai_scores": {
    "conv_001": {"score": 3, "reason": "直接提到语音快捷键"},
    "conv_002": {"score": 2, "reason": "与语音输入相关"}
  }
}
```

响应：

```json
{
  "id": "report_abc123",
  "status": "pending"
}
```

校验规则：

- `conversation_ids` 不能为空。
- `conversation_ids` 最大数量默认限制为 500。
- 创建任务时允许结果集很大，但实际 LLM 分析样本需要截断。

### 6.2 获取报告列表

`GET /api/search-reports?limit=20`

响应：

```json
{
  "items": [
    {
      "id": "report_abc123",
      "status": "succeeded",
      "title": "语音输入快捷键相关反馈分析",
      "search_type": "smart",
      "sample_count": 80,
      "created_at": 1781580000000,
      "started_at": 1781580005000,
      "finished_at": 1781580300000,
      "error_message": null
    }
  ]
}
```

### 6.3 获取报告详情

`GET /api/search-reports/{id}`

响应包含任务元信息、状态和 `result_markdown`。未完成时 `result_markdown` 为 `null`。

### 6.4 重试报告

`POST /api/search-reports/{id}/retry`

重试规则：

- 仅允许 `failed` 状态重试。
- 重试复用原始快照。
- 重试会清空 `error_message` 和旧的 `started_at / finished_at`。
- MVP 可直接复用原任务 ID；后续如需审计历史，可改为创建新任务并记录 `parent_job_id`。

## 7. 报告生成流程

后台任务执行步骤：

1. 将任务状态从 `pending` 更新为 `running`，写入 `started_at`。
2. 读取 `conversation_ids_json`。
3. 从 `feedback` 表拉取每个会话的全部反馈文本，按 `conversation_id, msg_seq` 排序拼接。
4. 合并会话元数据：
   - `conversation_label` 中的时间、版本、平台、标签字段
   - `feedback` 中的平台、版本、原始文本
   - `ai_scores_json` 中的相关性分数
5. 对样本排序：
   - 优先 AI 精筛分数高的会话
   - 其次按最近时间排序
6. 截取进入 LLM 的样本：
   - 默认最多 80 条会话
   - 每个会话文本最多 500 字
   - 总输入字符设置上限，避免超长报告任务失败
7. 生成客观统计：
   - 总命中会话数
   - 实际分析样本数
   - 时间跨度
   - 平台分布
   - 版本分布
   - AI 相关性分布
   - `L1 / L2 / severity` 弱参考分布
8. 调用 LLM 生成结构化 Markdown。
9. 写入 `result_markdown`，状态改为 `succeeded`，写入 `finished_at`。
10. 任一步失败则状态改为 `failed`，写入简短 `error_message`。

## 8. LLM Prompt 要求

系统角色：反馈分析报告生成器。

输入内容：

- 用户搜索问题或关键词条件摘要
- 本次搜索条件快照摘要
- 客观统计
- 按相关性排序后的代表反馈样本

输出格式固定为 Markdown：

```md
# 搜索反馈分析报告

## 结论摘要

## 关键发现

## 主要问题类型

## 典型用户反馈

## 数据概览

## 产品建议

## 局限性
```

约束：

- 必须说明报告基于本次搜索结果快照。
- 不得声称代表全部用户反馈。
- 不得把弱标签统计当成唯一事实来源。
- 关键发现尽量引用代表会话或反馈原文。
- 产品建议必须与反馈证据对应。
- 如果样本量不足，需要在局限性中说明。

## 9. 前端设计

### 9.1 入口

在反馈列表页搜索结果区域新增“生成报告”按钮。

展示规则：

- 没有搜索结果时禁用。
- 正在提交任务时显示提交中状态。
- 创建成功后展示最近任务状态提示。

### 9.2 创建确认面板

确认面板展示：

- 报告标题输入框，默认由 query 生成。
- 当前结果数。
- 本次提交的会话数。
- 预计分析样本上限。
- 搜索类型。
- 是否已包含 AI 精筛分数。
- 提示“报告可能需要几分钟，生成后可在报告面板查看”。

### 9.3 报告面板

报告面板展示最近任务列表。状态文案：

- `pending`：排队中
- `running`：生成中
- `succeeded`：已完成
- `failed`：生成失败

MVP 轮询策略：

- 当存在 `pending / running` 任务时，每 5 秒刷新一次列表或详情。
- 没有进行中任务时停止轮询。

### 9.4 报告详情

报告详情以 Markdown 渲染，支持：

- 复制 Markdown
- 重试失败任务
- 返回报告列表

后续可以增加“查看关联反馈”，MVP 可先不实现或只展示会话 ID。

## 10. 错误处理

- LLM 服务不可用：任务失败，提示“AI 服务暂时不可用，请稍后重试”。
- 样本为空：任务失败，提示“报告样本为空，请重新搜索后生成”。
- 输入过长：后端截断样本，并在报告局限性中说明。
- 会话 ID 部分不存在：跳过缺失会话，记录实际样本数；若全部缺失则失败。
- 后台任务中断：任务可能停留在 `running`。MVP 在列表或详情查询时检查运行时长，超过默认 15 分钟则标记为失败。超时时长通过配置项控制，后续由队列系统解决。

## 11. 测试策略

### 11.1 后端测试

- 创建报告任务时保存完整快照。
- 空 `conversation_ids` 返回 400。
- 报告列表按创建时间倒序。
- 报告详情返回状态和 Markdown。
- 失败任务可重试，且复用原快照。
- 生成流程按 AI 分数优先采样。
- LLM 失败时任务进入 `failed`。

### 11.2 前端测试

- 搜索结果为空时“生成报告”不可用。
- 创建确认面板展示正确的结果数和搜索摘要。
- 创建成功后报告面板出现新任务。
- 进行中任务触发轮询，完成后停止轮询。
- 报告详情可以复制 Markdown。
- 失败任务可以点击重试。

## 12. 分阶段实施

### 阶段 1：后端任务闭环

- 新增 `search_report_job` 表。
- 新增报告任务 CRUD API。
- 实现后台任务执行和 LLM 报告生成。
- 增加后端单元测试。

### 阶段 2：前端任务入口

- 在搜索结果区增加“生成报告”入口。
- 实现创建确认面板。
- 实现报告列表面板和状态轮询。
- 实现报告详情 Markdown 渲染。

### 阶段 3：体验补强

- 支持复制 Markdown。
- 支持失败重试。
- 优化报告标题和搜索摘要展示。
- 增加任务超时处理。

## 13. 后续演进

- 独立 worker / 队列：解决 CloudRun 或进程重启导致的任务中断。
- 报告历史审计：重试创建新 job，保留每次生成结果。
- 报告与反馈联动：点击报告证据跳转对应会话。
- 报告导出：Markdown、PDF 或飞书文档。
- 主动洞察：在搜索报告稳定后，扩展到日报、周报和异常上升分析。
- 标签增强：LLM 打标稳定后，将标签趋势作为报告中的增强信息。

## 14. 实现决策

本期不保留开放产品问题，避免实现时产生分歧。MVP 决策如下：

- 不实现删除报告任务，只支持查看和重试。
- 由于当前系统没有用户登录与权限边界，报告列表展示全局最近任务。
- 后台任务运行超时默认 15 分钟，可通过配置调整；CloudRun 部署下的实际中断风险在后续验证和队列化演进中处理。
