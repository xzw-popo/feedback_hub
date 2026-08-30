# 动态主题发现原型设计

日期：2026-07-15

## 1. 原型目标

用 2026-07-12 的真实会话问题单元验证以下命题：

1. 主题可以由当天反馈自下而上产生，而不是来自固定主题标签表。
2. 主题生成不依赖 `report_candidate`、`report_policy` 或预先确定的 `feature_id` 分桶。
3. 每个主题都能追溯到问题单元、原始 conversation 和反馈链接。
4. 生成的主题可以作为动态主题库的第一批 `provisional` 主题，并支持后续日期执行延续、新建、子主题和待复核判断。

本原型只验证“当日独立发现和主题库初始化”。由于当前媒体完整的新数据只有 2026-07-12，本轮不声称已经验证真实跨日新主题或上升趋势。

## 2. 输入口径

输入文件：

`feedback_hub/data/knot_label_eval/full_daily_20260712/knot_full_daily_conversation_742_20260713.jsonl`

从 688 个会话问题单元中选择：

- `unit_kind=semantic_issue`；
- `feedback_type` 属于 `bug_problem`、`feature_request`、`improvement_request`、`mixed`。

实际输入为 493 个问题单元。

明确不使用：

- `report_label`；
- `report_candidate`；
- `feedback_value_level`；
- `report_policy`；
- 旧聚类的 `cluster_id` 或成员关系。

`feature_id`、`feedback_type` 和 `issue_pattern` 只作为最终解释字段，不参与硬分桶或主题准入。

## 3. 主题定义

一个原子主题表示：

> 可以由同一次调查、修复或产品决策处理的一组问题单元。

同一功能下的不同症状、触发条件或处理方向应形成不同主题。不同平台上的表达只有在底层行为和预期处理相同时才合并。

主题不是固定标签。主题由成员问题单元生成，并具有生命周期、版本和来源。

## 4. 当日主题发现流程

### 4.1 构建问题单元表示

每个问题单元用于召回的文本由以下内容组成：

- `summary`；
- `evidence_spans`；
- `feedback_type` 的中文语义说明；
- 可用时附带平台和场景，但不附带旧报告判断。

使用本地 Qwen3-Embedding 在 CPU 上生成归一化向量。生成后必须校验：

- 向量数量与 493 个问题单元一致；
- 不存在 NaN 或 Inf；
- 每个向量范数接近 1；
- 问题单元 ID 无重复。

### 4.2 开放式相似召回

在全部 493 个问题单元之间进行近邻召回，不按功能或问题类型预分桶。

每个问题单元召回最多 10 个近邻，第一版保留余弦相似度不低于 `0.72` 的边。该阈值用于偏宽召回，向量只负责找出“可能属于同一主题”的候选，不直接决定主题归属。

候选图按有界连通分量拆成最多 20 个问题单元的模型任务。没有达到召回阈值的问题单元直接保留为单例主题候选。

阈值作为运行参数写入 manifest。第一版同时输出近邻分数，并在报告中补充 `0.76` 和 `0.80` 下的候选规模敏感性统计；这两个阈值只做离线对照，不改变主运行结果。

### 4.3 模型确认主题边界

Knot 模型只处理包含至少两个问题单元的候选组。模型必须：

- 按“同一个可行动问题”判断是否合并；
- 允许将候选组拆成多个主题；
- 允许保留单例；
- 不因为功能、平台或情绪相同而合并；
- 将每个输入问题单元分配且只分配一次；
- 输出主题标题、描述、成员 ID、置信度和复核标记。

解析器对遗漏成员自动补单例，但将其标记为 `parser_added_singleton=true` 和 `needs_review=true`。

### 4.4 生成当日主题候选

每个主题候选包含：

```json
{
  "daily_topic_id": "daily:2026-07-12:0001",
  "title": "Windows 语音输入结束后文字不上屏",
  "description": "语音识别结束后没有文字输出",
  "source_date": "2026-07-12",
  "member_issue_unit_ids": ["conversation:01"],
  "conversation_ids": ["conversation"],
  "conversation_count": 1,
  "feedback_type_counts": {"bug_problem": 1},
  "feature_candidate_counts": {"voice_input": 1},
  "platform_counts": {"Win": 1},
  "appversion_counts": {"2.1.0.36": 1},
  "evidence_links": ["https://..."],
  "confidence": 0.86,
  "needs_review": false
}
```

必须满足：493 个问题单元全部覆盖、无重复归属、无遗漏。

## 5. 动态主题库初始化

本轮没有历史主题库，因此所有当日主题候选都会产生 `topic_created` 事件，并初始化为：

```json
{
  "topic_id": "topic:000001",
  "topic_version": 1,
  "status": "provisional",
  "canonical_title": "Windows 语音输入结束后文字不上屏",
  "canonical_description": "语音识别结束后没有文字输出",
  "first_seen": "2026-07-12",
  "last_seen": "2026-07-12",
  "source_daily_topic_ids": ["daily:2026-07-12:0001"],
  "parent_topic_ids": [],
  "merged_into_topic_id": null
}
```

主题 ID 稳定，标题和描述可以随证据更新。主题变化通过事件记录，不原地抹掉历史。

## 6. 下一日期匹配接口

原型同时实现但不伪造运行结果的接口：

```python
match_daily_topics(
    daily_topics: list[DailyTopic],
    historical_topics: list[Topic],
) -> list[TopicLifecycleEvent]
```

事件类型：

- `topic_created`：没有历史主题可以承接；
- `topic_extended`：确认是已有的同一可行动问题；
- `possible_subtopic`：相关但症状、场景或处理方向不同；
- `match_uncertain`：证据不足，暂不归入旧主题；
- `topic_renamed`：新增证据使主题描述需要更新；
- `topic_merged`、`topic_split`：为后续周期维护预留，不在首日自动执行。

匹配流程为：向量召回历史主题候选，再由模型输出 `same_topic`、`new_topic`、`possible_subtopic` 或 `uncertain`。向量相似度不能直接更新主题成员关系。

## 7. Review 产物

原型输出到独立实验目录：

`feedback_hub/data/topic_discovery_prototype/daily_20260712/`

包含：

- `issue_units_493.jsonl`：本轮输入快照；
- `issue_unit_embeddings.npz`：本地向量；
- `similarity_candidates.jsonl`：近邻及相似度；
- `cluster_buckets.jsonl`：送给 Knot 的候选组；
- `daily_topics.jsonl`：当日独立发现的主题；
- `topic_events.jsonl`：首日 `topic_created` 事件；
- `topic_store.jsonl`：初始化后的动态主题库；
- `topic_discovery_review.xlsx`：人工 review 表；
- `topic_discovery_report.md`：数量漏斗、主题分布、代表案例和与旧流程的差异。

Review 表至少包含：

- 主题 ID、标题、描述；
- 去重会话数；
- 问题单元摘要；
- 平台、版本、功能候选；
- 原会话链接；
- 模型置信度和复核原因；
- `human_boundary_result`、`human_notes` 空列。

## 8. 验收标准

### 数据完整性

- 输入恰好为 493 个符合口径的问题单元；
- 每个问题单元只属于一个当日主题；
- 主题成员总数为 493；
- 所有证据链接可追溯到原 conversation；
- 不读取旧 `report_label` 或旧聚类成员关系。

### 主题质量

- 多会话主题不存在仅因功能相同而过度合并的明显案例；
- 相同问题的改写和重复表达能够合并；
- Bug 和需求默认分开，除非确实描述同一缺失行为；
- 模糊或媒体依赖问题不会被模型猜测性合并。

### 可解释性

- 每个主题能展示成员摘要和原会话链接；
- 每个 `topic_created` 事件能指回产生它的当日主题；
- 报告明确区分“新建临时主题”和“值得进入日报的新信号”。

## 9. 本轮不做

- 不重新设计混合检索 Skill；
- 不生成正式日报；
- 不把 2026-07-12 主题宣称为历史新增主题；
- 不判断上升趋势；
- 不自动合并或拆分历史正式主题；
- 不用固定主题表限制开放发现；
- 不把所有单例主题都当作高价值反馈。

## 10. 与旧流程的对照目标

原型完成后，需要与旧的 411 个主题比较，但旧结果只作为对照，不作为正确答案。重点比较：

- 新流程是否减少了由 `unknown_feature` 大桶导致的边界问题；
- 是否找回了被 `report_policy` 排除但实际可形成产品主题的问题；
- 是否出现跨功能误合并；
- 多会话主题的可行动性是否更一致；
- 模型调用数和总耗时是否低于“联合打标 + 报告判断 + 聚类”链路。
