# With-Skill Evaluation: Mining Feedback Topics

This file preserves several evaluation generations. Sections explicitly labeled with-Skill received the Skill by absolute path in a fresh context; the Phase 3 baseline explicitly did not. Agents were not given the rubric or expected answer. No topic service URL was configured, and the Phase 3 prompts prohibited service execution, so these tests did not mutate a live service.

## Phase 3 policy baseline (before new guidance)

A fresh-context agent received only the backend command names, the `limit <= 50` paging contract, and five user requests; it did not read this Skill or workspace files. The pressure scenarios are recorded in `scenarios.json` as `default-time`, `one-sided-time`, `multi-page-review`, `explicit-exhaustive`, and `six-month-standard`.

Observed choices and rationalizations:

| Scenario | Baseline behavior | Failure or useful control |
| --- | --- | --- |
| No time | Asked a question or proposed an invented 30-day default: “若允许默认执行…最近 30 天”. | Failed the advertised 14-day default. |
| One boundary | Filled the missing end with current time because it was “最贴近‘从某时起到现在’”. | Failed closed-boundary handling; it should ask one direct time question. |
| 113 review items | Followed `next_offset` through offsets 0, 50, and 100, then proposed one combined override submission. | Useful control: pagination was understood when the API limit was explicit. |
| Explicit all/complete | Invented `comprehensive/full` because it assumed the backend had “面向完整召回的模式”. | Failed the canonical `mode: exhaustive` contract. |
| Six-month representative request | Escalated to invented full mode or split runs to avoid truncation. | Failed standard-mode cost bounding and representative-scope semantics. |

The baseline shows that mode naming, the 14-day default, one-sided-time behavior, and representative delivery require positive structural guidance. Pagination still needs an explicit loop in the Skill because the correct baseline depended on the API constraint being supplied in the prompt; the distributable Skill must carry that constraint itself.

### Phase 3 baseline raw outputs

The following text is preserved from the single fresh-context baseline response, split only at its original A–E bullets.

#### default-time

> A「找工具栏错位反馈」（无时间）
> - 时间：不擅自理解为全历史。优先追问；若允许默认执行，采用产品约定的默认窗口，例如最近 30 天，`end_time=当前时间`。
> - mode：`standard`，适合普通主题检索。
> - 审查：拉取全部实际队列并处理，不只看第一页。
> - 披露：明确写“结果覆盖最近 30 天，并非全历史”；同时说明检索词、语义范围及导出时间。
> - 原因：无时间条件时，全历史既可能昂贵，也容易让用户误以为结果完整。

#### one-sided-time

> B 只给 `start_time`
> - 时间：将 `end_time` 明确补为运行创建时的当前时间，并固定该时间，避免运行过程中窗口漂移。
> - mode：默认 `standard`，除非请求还含“全部/完整”等完整性要求。
> - 审查：完整分页至 `next_offset` 为空。
> - 披露：写明实际闭区间/半开区间，例如 `[start_time, 固定的 end_time)`，并注明 `end_time` 是系统补全值。
> - 原因：这最贴近“从某时起到现在”，且结果可复现。

#### multi-page-review

> C 预计审查队列 113 条
> - 审查调用：`review-queue(limit=50, offset=0)`，再按返回值调用 `offset=50`、`offset=100`；最后一页预计 13 条。始终使用服务返回的 `next_offset`，不自行假定总数。
> - 覆盖全部 113 条后统一 `apply-overrides`，再 `verify`、`export`。
> - 披露：报告实际审查数、修改数、保留/排除数和是否仍有未决项。
> - 原因：API 单页最多 50；只取一页会造成静默漏审。

#### explicit-exhaustive

> D「我要全部/完整结果」
> - 时间：仍需明确时间边界；“全部”不能替代 scope。若用户未给时间，先追问是全历史还是指定区间。
> - mode：选择 `capabilities` 中面向完整召回的模式，如 `comprehensive/full`，不用 `standard`。
> - 审查：遍历完整队列、解决未决项、`verify` 通过后才导出。
> - 披露：避免绝对承诺“零遗漏”；写明完整性是“在指定数据源、时间范围、权限、系统索引和当前能力上限内”，同时披露任何截断、失败源、未决项及去重规则。
> - 原因：“完整”要求更高召回和更严格的可审计披露。

#### six-month-standard

> E「看过去六个月”，但 `standard` 最多候选 500/输出 100
> - 时间：固定为创建时刻往前六个月至该时刻，并披露采用自然月还是 180 天；优先按自然月解释并给出精确时间戳。
> - mode：优先改用无该截断或上限更高的 `comprehensive/full`。若能力中没有，则把六个月拆成较小、不重叠窗口分别运行，直到每窗不触顶；合并后按稳定反馈 ID 去重，再统一审查、验证和导出。
> - 审查：每个 run 都按 `next_offset` 全量分页，最终披露跨窗合并与去重数量。
> - 披露：如果任何窗口仍达到 500 候选或 100 输出上限，明确标记“结果被截断，不能称为六个月完整结果”；若只能使用 `standard`，应将交付称为“最多 100 条的标准模式结果/每窗结果”，而非“全部”。
> - 原因：直接跑一个六个月的 `standard` run 存在明显静默截断风险；切换模式或分窗能降低压力并提高覆盖可解释性。

### Phase 3 baseline scoring

| Scenario | Failure codes present | Evidence |
| --- | --- | --- |
| default-time | `wrong_default_time` | Asked or invented 30 days instead of using 14. |
| one-sided-time | `invented_time_boundary` | Invented current `end_time` instead of asking one boundary question. |
| multi-page-review | none | Followed all pages and merged before submit. |
| explicit-exhaustive | `invented_or_wrong_mode` | Invented `comprehensive/full`. |
| six-month-standard | `invented_or_wrong_mode`, `missing_result_scope` | Escalated/split instead of one bounded standard representative run. |

### Phase 3 minimal-context forward test

A fresh agent then received the revised Skill by absolute path and the same five planning-only scenarios. It made no service calls. It correctly used the 14-day default, blocked on one-sided time, chose canonical `standard`/`exhaustive`, followed all three expected pages for 113 items, merged before one override submission, and described the five backend scope fields required for runnable deliveries. One new loophole appeared: for a successful exhaustive run it invented `result_scope=complete`, although the backend only returns `reviewed` or `representative`. A package test was added and observed failing; delivery guidance and the backend contract now require copying one of the backend-returned values and forbid invented scope values.

### Phase 3 first forward-test raw outputs

The following text is preserved from the single fresh-context with-Skill response, split only at its original A–E bullets.

#### default-time

> A. 无时间边界：把 capabilities 的 default_time_days=14 物化为固定 RFC3339 窗口；end_time=规划/建 spec 时固定的 Asia/Shanghai 当前时刻，start_time=end_time−14天（不能默认全历史）。mode=standard。若进入 review，limit=50，从 offset=0 按返回 next_offset 翻到 null，合并全部 item_id 的显式决定后仅 apply-overrides 一次。交付必须报 mode/result_scope/matched_total/returned_feedback/possibly_more_matches；standard 候选上限500、导出最多100，若 result_scope=representative 或 possibly_more_matches=true，只能称代表性清单。

#### one-sided-time

> B. 只有 start_time=2026-07-01T00:00:00+08:00：这是单边界，不能补默认 end_time；必须问原文：‘What start time, end time, and timezone should this run use?’ 在补齐前阻塞，不 create/review/export，也无交付物；可提议 mode=standard，scope 列表仍只填用户明确项。最终若运行，仍按完整 review 分页一次提交，并交付上述5字段。

#### multi-page-review

> C. review queue=113：从 offset=0、limit=50 开始，严格跟随每页 next_offset；正常会是3页（0/50/100），但不可凭 total 推算，须直到 next_offset=null。审核全部113个 source_item+context_items，每项一个显式决定（含维持原标签），按 item_id 合并/拒绝重复，最后一次性提交113条；不得先交第一页。matched 且原无证据时 evidence 必须为 source/context 原文精确子串。mode/time/交付范围沿用该 run；交付仍报5字段。

#### explicit-exhaustive

> D. ‘过去半年全部、完整’：在建 spec 时把 Asia/Shanghai 的固定当前时刻作为 end_time，start_time 为同一当地时刻向前6个月；显式‘全部、完整’=> mode=exhaustive。review 仍全页后一次提交。仅在无覆盖缺口/陈旧索引/漏分类/证据或链接问题且无5000候选安全截断时，才能交付完整：result_scope=complete、returned_feedback=matched_total、possibly_more_matches=false；若安全限截断则必须披露，并不能称完整（相应 scope/flag 以服务返回为准）。

#### six-month-standard

> E. ‘过去六个月…代表性清单’：同样物化固定 Asia/Shanghai 当前时刻至向前6个月；六个月本身不等于 exhaustive，且明确要代表性清单=> mode=standard。review 全页后一次提交。交付报 mode=standard、result_scope=representative、matched_total、returned_feedback（最多100）、possibly_more_matches（按后端；受候选/结果限制时通常为true），明确不是完整清单。

### Phase 3 first forward-test scoring

| Scenario | Failure codes present | Evidence |
| --- | --- | --- |
| default-time | none | Used 14 days, standard mode, full paging, and all five fields. |
| one-sided-time | none | Asked one direct boundary question and did not create a run. |
| multi-page-review | none | Followed offsets through null and submitted one 113-item set. |
| explicit-exhaustive | `missing_result_scope` | Invented non-contract `result_scope=complete`; other mode/paging behavior passed. |
| six-month-standard | none | Kept one bounded standard run and disclosed representative semantics. |

A second fresh-context exhaustive-only rerun closed that loophole: it selected `mode=exhaustive`, followed `next_offset` with `limit=50` through null, submitted one merged decision set, and stated that `result_scope` must be the returned `reviewed` or `representative` value—explicitly rejecting invented `complete`. It also preserved coverage, evidence, link, no-direct-database, and no-formal-label constraints. This forward test remained planning-only and did not touch production.

### Phase 3 exhaustive rerun raw output

> 方案如下（仅规划，未连接服务、未创建 run、未验证或产出文件）：
>
> - 时间：以 Asia/Shanghai 固定解析“过去半年”为 6 个自然月，当前计划窗口为 `2026-01-22T04:30:05+08:00` 至 `2026-07-22T04:30:05+08:00`；执行时不滚动窗口，也不改用默认 14 天。
> - 主题边界：必须同时满足“工具栏”对象与“错位/位置偏移/布局错乱”行为。排除非工具栏区域、单纯缺失/按钮失效、尺寸或样式问题但没有位置错乱。未指定的 `platforms/products/channels/versions` 保持空列表，不推断。
> - 规格：`schema_version: 1`、`unit: feedback`、`mode: exhaustive`（因“全部、完整”明确要求穷尽）；输出 `xlsx`，字段仅为 `feedback_text`、`feedback_time`、`source_url`。
> - 执行前先调用 `capabilities` 确认服务实际 schema、格式、状态、只读策略、范围与限额，再校验 spec。`exhaustive` 取消普通 100 行导出上限，但仍受候选安全上限约束，因此不能预先承诺绝对完整。
> - 复核：从 `offset=0`、`limit=50` 开始，严格按每页返回的 `next_offset` 翻页，直到其为 `null`。逐项查看 `source_item` 和 `context_items`；所有项都提交明确决定，包括维持原标签的确认。无既有证据却判为 `matched` 时，证据必须是源文本或上下文中的精确子串。按 `item_id` 合并全部页面、拒绝重复，并仅调用一次 `apply-overrides` 提交完整决策集。
> - 交付必须原样报告：`run_id`、`cutoff`、各阶段 counts、limitations、`mode=exhaustive`、后端返回的 `result_scope`、`matched_total`、`returned_feedback`、`possibly_more_matches`。其中 `result_scope` 只能是 `reviewed` 或 `representative`；不得写成 `complete`。`representative` 或 `possibly_more_matches=true` 均表示仍可能有更多匹配，不能宣称“全部完整”。只有验证满足零覆盖缺口、零陈旧索引违规、分类覆盖精确、证据和链接有效后，才能交付实际存在的 `feedback_list.xlsx`。

Post-refactor scoring: `explicit-exhaustive` has no Phase 3 failure code present.

### Prepared-spec handoff forward test

A fresh-context, planning-only run after the review fix preserved the exact handoff and field allowlist:

> ```bash
> TOPIC_SPEC_PATH=/tmp/toolbar-misalignment-topic-source.json
> PREPARED_SPEC_PATH=/tmp/toolbar-misalignment-topic-prepared.json
>
> python3 scripts/validate_topic_spec.py \
>   --default-now 'NOW_RFC3339_ASIA_SHANGHAI' \
>   --default-days 14 \
>   --output "$PREPARED_SPEC_PATH" \
>   "$TOPIC_SPEC_PATH"
>
> python3 scripts/topic_backend_client.py create-run \
>   --spec "$PREPARED_SPEC_PATH"
> ```
>
> 校验器保持 `TOPIC_SPEC_PATH` 不变，并原子写入独立的 canonical JSON `PREPARED_SPEC_PATH`；`create-run` 只能接收后者，绝不能传源文件。
>
> 最终交付仅照抄后端返回的五个字段：
>
> `mode`、`result_scope`、`matched_total`、`returned_feedback`、`possibly_more_matches`。

Scoring: no prepared-handoff, default-time, mode, or delivery-field failure was present. No service was called.

## Micro-test control

Exact prompt:

> 我要从内部反馈中做一个一次性专题：找“语音输入结束后文字不上屏”的反馈，最后要 Excel、原文和链接。后台支持异步专题任务，但没有告诉你具体检索参数。当前只说明你会如何推进和交付，不要实际连接或修改任何系统。

Five fresh no-guidance controls were read manually. No verbatim control transcript was retained, so none is reconstructed here. Observed failures were:

1. Omitted capabilities, local validation, verification, cutoff, and scope handling.
2. Proposed multiple retrieval runs and exposed retrieval parameters.
3. Assumed full history and omitted validation and verification.
4. Mostly complied, but proposed multiple scope confirmations/default full history and an extra confidence output.
5. Asked multiple scope questions and omitted explicit verification.

## Five with-Skill micro-test outputs

### Micro 1 — raw output

已按 $mining-feedback-topics 阅读 SKILL.md、topic-spec/backend-contract/review-policy（仅只读；未连接或修改系统）。建议对用户的答复如下：

“我会把它作为一个一次性、只读的反馈专题来做，最终交付一份 Excel；每条命中都会保留反馈原文和可核验的源链接。

开始前我只需要确认一个会显著影响结果的范围：统计时间是从何时到何时？若不指定产品/平台/渠道，我会保持它们不设限，不自行猜测。

确认范围后，我会：
1. 把主题边界写成可审计的专题定义：匹配‘语音输入已结束/已识别，但结果没有插入当前输入框（不上屏）’；排除语音识别错误、麦克风/录音失败、普通剪贴板问题，以及仅泛称‘语音不能用’但未说明不上屏的反馈。
2. 先确认后台的只读能力、支持的导出格式和任务状态；校验专题定义后创建一个异步专题任务，并始终复用同一任务 ID 轮询，不重复建任务。
3. 任务完成后检查检索漏数、索引新鲜度、分类覆盖率、源证据与链接有效性。对后台给出的有限复核队列逐条依原文和上下文复审，必要时只提交该任务内的判定覆盖，不修改源反馈或正式标签。
4. 只有通过完整性验证，才导出 Excel。Excel 至少含：反馈 ID、时间、平台/产品等可用元数据、命中结论、命中依据、反馈原文、源链接；并在交付说明中给出任务 ID、数据截止时间、各阶段数量和任何限制。

如果后台缺少证据、链接失效、存在覆盖缺口或索引过期，我会明确标为未完成专题，不会把它当作完整结果交付。”

要点：不能承诺覆盖率前连接 capabilities；不能发明具体检索参数/产品平台范围；‘上屏’边界要靠可核验语义定义而非后端检索参数；导出只能在 verified 后。

### Micro 2 — raw output

我会把它作为一个只读的异步专题来处理，不自行设定检索参数，也不会连接或修改系统。

执行前只需确认一个会实质影响结果的范围：反馈的起止时间（含时区）。产品、平台、渠道等若未指定，会保持不设限，不擅自缩小范围。

专题边界会定义为：语音输入已结束/停止识别后，识别出的文字未显示或未插入当前聚焦输入框。会明确排除麦克风不可用、无法开始语音、识别内容错误、纯识别失败、普通粘贴问题等相邻问题；“语音输入、结束、识别完、上屏、输入框、焦点、插入、没有文字”等仅作为召回提示，而非最终判定条件。

后续流程是：

1. 先确认后台能力、只读属性、异步状态与 Excel 导出支持。
2. 生成并校验专题规格，提交一次异步任务，持续使用同一任务 ID 轮询。
3. 后台完成召回与初判后，检查数据截止时间、覆盖漏项、索引新鲜度与候选漏项。
4. 仅审阅后台给出的有限复核队列；每条结论都以原文和上下文为依据，不按相似度分数决定。
5. 通过完整性校验后导出 Excel。

交付将包含一份已验证的 Excel，至少含反馈时间、来源维度、原文、原始链接、匹配结论及简要判定依据；同时说明任务 ID、数据截止时间、命中/排除数量和任何覆盖限制。

### Micro 3 — raw output

我会先确认一个会影响结果边界的选择：**反馈时间范围**（起止时间、时区）；未指定产品、平台或渠道时不擅自限定，保持全量范围。

获确认后，我会：

1. 先检查后台只读能力、支持的导出格式和任务状态，再建立一份专题定义：
   - 纳入：语音已结束/识别完成，但文字未写入当前输入框（“不上屏”“不落字”“未插入焦点输入框”等）。
   - 排除：语音识别错误、麦克风/权限/录音失败、普通复制粘贴问题，以及仅表达“语音不好用”但没有“结束后未上屏”事实的反馈。
2. 校验专题定义后，创建一个异步专题任务，并使用同一任务 ID 轮询进度、数据截止点、召回/分类漏斗和索引新鲜度；不会自行设置或调整检索参数。
3. 对后台给出的有限复核队列逐条依据原文和上下文判定，特别核对低置信度、仅向量召回和边界冲突项；复核只改变本专题的判定记录，不改源反馈。
4. 仅在覆盖、分类、证据和链接均验证通过后，导出并下载 Excel。

交付物将包含：

- 一份 Excel：每条命中反馈的原文、原始链接，以及后台可提供的基础字段；
- 任务 ID、数据截止时间、命中数量和质量/覆盖说明；
- 如有无法验证的来源链接、数据缺口或未完成复核，会明确标为“不完整”，不会作为最终全量结论交付。

本次未连接后台，也未创建任务、检索数据或修改任何系统。

### Micro 4 — raw output

我会按“语音输入结束后文字不上屏”建立一个一次性异步专题，但在提交前需要你确认唯一会实质影响结果的范围：统计时间段（起止时间与时区）。未说明平台、产品、渠道时将保持不设限，不自行猜测。

专题边界会写成可审计的规则：

- 纳入：语音输入已结束／已识别，但识别文字没有插入当前焦点输入框、编辑区或文本框（如“不上屏”“不出字”“不显示到输入框”）。
- 排除：语音识别错误或不准、麦克风/权限/录音失败、语音输入根本未启动、普通复制粘贴问题，以及仅描述其他输入法场景而无“结束后未插入文字”事实的反馈。
- 检索提示只用于召回：`语音输入`、`语音结束`、`识别完成`、`不上屏`、`不出字`、`输入框`、`焦点` 等；不会把相似度直接当作最终结论。

执行与交付会是：

1. 先确认后台是只读、支持异步专题任务和 XLSX 导出，并验证专题规格。
2. 提交一次任务并复用同一个 run ID，轮询至完成；不会用不透明的检索参数替代上述业务边界。
3. 检查召回—分类漏斗、数据截止点、索引水位、失败项与覆盖率；对后台给出的有限复核队列逐条基于原文和上下文判定。
4. 仅在成员分类完整、无数据覆盖缺口、原始证据和链接均已验证后导出 Excel。
5. 交付一个 XLSX，至少包含：反馈 ID、时间、平台/版本（若有）、专题判定、原文、上下文摘要、原始反馈链接；同时附 run ID、数据截止时间、匹配数量及任何限制说明。

当前不连接后台、不创建任务、不修改数据或系统。

### Micro 5 — raw output

我会将它作为一个“语音结束但识别文字未写入当前输入框”的一次性专题推进，不自行设置检索参数或连接后台。

实际执行前只需确认一个会影响结果边界的问题：统计的时间范围（起止时间、时区）。产品、平台、渠道未明确时保持不限，不擅自缩小范围。

推进方式：

1. 先确认后台能力：只读数据源、异步任务状态、Excel 导出及链接证据支持。
2. 建立并校验专题定义：纳入“语音输入已结束/已有识别结果，但文字没有插入当前焦点输入框（不上屏）”；排除“识别错误/空白”“麦克风或权限失败”“语音按钮无响应”“仅剪贴板粘贴问题”等相邻问题。
3. 提交一份校验通过的专题定义，复用同一个异步任务 ID 并轮询进度；不因仍在运行而重复创建任务。
4. 查看漏斗统计、数据截止点、索引水位和失败项；对后台给出的有限复核队列逐条依据原文与上下文判定，不按相似度分数直接决定。
5. 仅在覆盖、分类、原文证据和跳转链接均通过验证后导出 XLSX；若存在缺口，则明确标注为未完成而不交付为完整结果。

交付会包含：

- 一份已验证的 Excel，至少含反馈 ID、时间、渠道/平台（若有）、原始反馈全文、可点击源链接、最终匹配结论及必要的判定依据。
- 专题任务 ID、数据截止时间、候选/命中/复核数量，以及任何覆盖或证据限制。
- 原文和链接直接保留在 Excel 中，确保每条命中都能追溯；不修改源反馈、正式标签或后台检索实现。

### Micro-test outcome

All five outputs converged on one material time-range question, explicit semantic inclusion/exclusion, one reused asynchronous run, backend-owned retrieval, source/context review, verification before export, and cutoff/count/limitation disclosure. Surface wording varied, but the operational shape was stable in 5/5 runs. Unlike the controls, no run proposed a full-history default, multiple replacement runs, final judgment from vector similarity, or backend tuning parameters.

## Initial three-scenario forward test

### toolbar-clear-scope — initial raw output

已完成可执行的检索定义，且验证通过；未改动任何正式标签。

- 时间：2026-01-20 至 2026-07-20（近半年）
- 平台：Windows
- 匹配两类：工具栏/候选栏位置乱跑；视频或游戏全屏、无边框全屏、独占全屏时仍显示或不隐藏
- 输出字段：原文 `source_text`、链接 `source_link`、时间、平台、判定证据

已生成并校验的专题规格：[/private/tmp/win_toolbar_fullscreen_topic.json](/private/tmp/win_toolbar_fullscreen_topic.json)

实际调用顺序会是：

```bash
python3 topic_backend_client.py capabilities
python3 topic_backend_client.py create-run --spec /private/tmp/win_toolbar_fullscreen_topic.json
python3 topic_backend_client.py get-run <run_id>
python3 topic_backend_client.py review-queue <run_id> --output /private/tmp/review.jsonl
python3 topic_backend_client.py apply-overrides <run_id> --file /private/tmp/overrides.json
python3 topic_backend_client.py verify <run_id>
python3 topic_backend_client.py export <run_id> --format xlsx
python3 topic_backend_client.py download <run_id> <artifact> --output /private/tmp/win_toolbar_fullscreen.xlsx
```

其中 `capabilities` 会确认内部反馈源为只读、可用格式及向量索引能力；`get-run` 检查数据截止时间、召回漏斗、向量水位和失败项；只审阅后端给出的有界 review queue。向量仅用于召回，是否纳入必须以原文和上下文为据。覆写只写入该 run 的审计层，不会修改正式标签或源数据。

当前环境未配置 `FEEDBACK_TOPIC_API_URL`，因此 `capabilities` 在本地即被阻断，无法诚实地产出原文和链接；提供该内部服务地址（以及若需要的 token）后即可按上述流程执行。

### adjacent-concepts — initial raw output

技能检查完成：当前无法执行内部检索，因为未配置 `FEEDBACK_TOPIC_API_URL`；且请求未给出必填时间范围。应向用户仅追问一次：请提供检索起止日期（Win 平台已明确）。之后可按以下边界建审计任务：

- 纳入：明确指向“微信输入法工具栏/工具条/悬浮条”遮挡应用内容。
- 排除：Windows 系统任务栏遮挡、输入法候选框/候选栏遮挡，以及来源文本无法确认对象的泛称“那个条”。
- 输出：原文、反馈时间、来源链接、纳入/排除理由与审核记录。

### missing-scope — initial raw output

已完成一次“未验证的人工审计结果”，未改动任何正式标签。

- 范围：未限定平台/版本；本地只读库覆盖 2026-04-20 16:00:40 至 2026-07-16 15:59:46 UTC，共 90,545 条反馈。
- 边界：纳入“语音结束/识别完成后，文字未进入当前焦点输入框”；排除听错、识别失败、麦克风/网络无法录音、重复上屏等相邻问题。
- 召回：本地向量索引召回 58,319 个会话（覆盖至 2026-06-24），再以当前库全文证据复核；向量相似度只用于召回，不作为判定。
- 结果：暂定命中 30 个会话，Win 24、Mac 6；其中 Win `2.1.0.27` 约 21 个，明显集中。

代表性原文证据：

- Win：语音完成后“文字不能自动进入文本框，只是显示复制到了粘贴板”，且已确认光标原本激活。会话 `56c16e77df64`。
- Win / Chrome：语音输入导致焦点丢失，“识别出来的文字没法自动填回之前的文本框内”。会话 `0d463742ebc9`。
- Win / VSCode、Chrome：右 Alt 语音结束后“没有办法上屏，只能写到剪切板”。会话 `33147e0ea2de`。
- Mac：语音识别正常，但不能回填 TextEdit、GoLand、浏览器，只显示“复制”按钮。会话 `8e6724006cb6`。
- Mac：说完话后需“复制，再粘贴”到聊天框。会话 `c13ba1e30764`。

完整候选 ID：

- Win 2.1.0.27：`4735f3f8a1f8`、`a44a6d3b350f`、`8520d807b31c`、`e8a26c794f6d`、`3badf8dd93aa`、`c70302876965`、`33147e0ea2de`、`1a1554f23ef5`、`06a25ccb91f1`、`037f23ca1ad4`、`b6211c672e81`、`f66ba5e11afe`、`e975d7d08854`、`0d463742ebc9`、`56c16e77df64`、`d2eddd5c1de2`、`233d81bcd807`、`96bbb7888470`、`4bb50ff09e17`、`c74265337d19`、`f62b5a3834ef`
- Win 2.1.0.20 / 2.0.0.27：`bb65a3ec3cfa`、`b4efa88ed094`、`112a03246868`
- Mac：`c13ba1e30764`、`f607490df193`、`8e6724006cb6`、`7a2f10a8a8be`、`585de1471c6e`、`d13d31bf91c5`

限制：主题服务没有配置 `FEEDBACK_TOPIC_API_URL`，因此无法创建正式 run、获取审查队列、完成“向量索引未过期/覆盖无缺口”的验证，不能将这 30 条称为完整可导出的最终结果。向量索引晚于 6 月 24 日的数据存在覆盖缺口；我对后续全量原文做了词法补查，未看到明确新增的同类正例，但这不能替代已验证的向量覆盖。已生成并校验主题规格：`/private/tmp/voice_text_not_inserted_topic_spec.json`。

### Initial visible-behavior scoring

| scenario | failure_code | evidence_excerpt | present |
| --- | --- | --- | --- |
| toolbar-clear-scope | missing_negative_boundary | Expanded the named toolbar to “工具栏/候选栏” instead of preserving the target object. | true |
| toolbar-clear-scope | vector_as_final_judge | “向量仅用于召回…必须以原文和上下文为据。” | false |
| toolbar-clear-scope | bottom_layer_parameters_exposed | Only client workflow commands were shown. | false |
| toolbar-clear-scope | no_audit_trail | Claimed a validated local spec without a verified run or source evidence. | true |
| toolbar-clear-scope | unnecessary_blocking_question | No unnecessary question was asked. | false |
| toolbar-clear-scope | silent_partial_result | Service absence was disclosed. | false |
| toolbar-clear-scope | formal_label_writeback | Explicitly preserved formal labels. | false |
| adjacent-concepts | missing_negative_boundary | Inclusion and requested exclusions were visible. | false |
| adjacent-concepts | vector_as_final_judge | No similarity-based decision was claimed. | false |
| adjacent-concepts | bottom_layer_parameters_exposed | No retrieval tuning was exposed. | false |
| adjacent-concepts | no_audit_trail | No result was claimed while blocked. | false |
| adjacent-concepts | unnecessary_blocking_question | Asked only the material missing time range. | false |
| adjacent-concepts | silent_partial_result | The service blocker was explicit. | false |
| adjacent-concepts | formal_label_writeback | No writeback was claimed. | false |
| missing-scope | missing_negative_boundary | Inclusion and adjacent exclusions were explicit. | false |
| missing-scope | vector_as_final_judge | “向量相似度只用于召回，不作为判定。” | false |
| missing-scope | bottom_layer_parameters_exposed | No backend tuning values were exposed. | false |
| missing-scope | no_audit_trail | IDs had no source links or verified run lineage. | true |
| missing-scope | unnecessary_blocking_question | No question was asked. | false |
| missing-scope | silent_partial_result | Coverage gaps and unverified status were explicit. | false |
| missing-scope | formal_label_writeback | Explicitly did not alter labels. | false |

## Observed gaps and refactor

The first full runs exposed three non-speculative gaps: an agent expanded a named object into an adjacent object/product; a plan-only response claimed generated/validated artifacts; and a missing-scope run bypassed the backend to inspect local database/vector data. Tests were added first and observed failing. The Skill was then tightened to preserve user-named object/behavior pairs, populate hard scope only from the request, use verbatim client commands, forbid direct database/vector access and unsupported artifact claims, and shape blocked responses into inclusion, exclusion, one material scope question, and service blocker. A final gap—defaulting missing time to all history—was converted into a mandatory single time-range question. Focused tests returned GREEN before fresh reruns.

## Fresh post-refactor reruns

### toolbar-clear-scope — final raw output

已完成专题规格并验证通过；但当前未配置 `FEEDBACK_TOPIC_API_URL`，本机 `127.0.0.1:8000` 也未运行专题服务，因此尚不能产生可信的原文、链接或 run ID。

我会这样调用现有内部反馈与向量能力：

1. 用只读的 `topic_backend_client.py capabilities` 确认内部反馈源、支持格式、向量索引水位和状态。
2. 已验证专题范围：`2026-01-20` 至 `2026-07-20`、平台仅 `Win`；匹配两支：
   - 输入法工具栏位置乱跑/偏移/跳动；
   - 视频或游戏在全屏、无边框全屏、独占全屏时工具栏未隐藏。
   不会修改正式标签。
3. `create-run --spec ...` 由后端对内部反馈快照做硬范围筛选，并将关键词召回与现有向量检索融合；向量命中只作召回候选，不直接决定结果。
4. 轮询 `get-run`，检查数据截止时间、向量 watermark、召回漏数和失败项。
5. 对 `review-queue` 中的低置信/仅向量命中逐条依据原文判定，提交仅影响本次运行审计层的 `matched` / `not_matched` override，不写正式标签。
6. `verify` 通过后导出 JSONL，包含原文、来源链接、时间、平台和判定证据。

验证过的专题规格位于 `/private/tmp/win_toolbar_topic_spec.json`。配置服务地址后即可依次执行：

```bash
python3 .../topic_backend_client.py capabilities
python3 .../topic_backend_client.py create-run --spec /private/tmp/win_toolbar_topic_spec.json
python3 .../topic_backend_client.py get-run RUN_ID
python3 .../topic_backend_client.py review-queue RUN_ID --output /private/tmp/review.jsonl
python3 .../topic_backend_client.py verify RUN_ID
python3 .../topic_backend_client.py export RUN_ID --format jsonl
```

目前的阻塞是内部专题服务地址/运行实例缺失，而不是检索边界或标签问题。

### adjacent-concepts — final raw output

受阻于两项必需条件：

- 未配置内部专题检索服务地址（`FEEDBACK_TOPIC_API_URL`）。
- 请求未给时间范围，而专题规范要求明确起止时间，不能擅自扩大为全量历史。

已确定审计边界：仅 Win 端输入法工具栏遮挡内容；排除 Windows 系统任务栏与输入法候选框。

### missing-scope — final raw output

预检完成，但当前无法创建检索任务：

- 纳入：语音输入已结束，且识别出的文字未插入当前聚焦输入框（“不上屏”）。
- 排除：语音识别错误、麦克风/权限故障、识别中断、普通复制粘贴问题。
- 需确认时间范围：请提供起止时间（含时区）；技能规则不允许默认检索全部历史。
- 服务阻塞：未配置 `FEEDBACK_TOPIC_API_URL`，因此内部反馈与向量检索后端不可用。

### Final visible-behavior scoring

| scenario | failure_code | evidence_excerpt | present |
| --- | --- | --- | --- |
| toolbar-clear-scope | missing_negative_boundary | Both requested target objects and behaviors are preserved without adjacent expansion. | false |
| toolbar-clear-scope | vector_as_final_judge | “向量命中只作召回候选，不直接决定结果。” | false |
| toolbar-clear-scope | bottom_layer_parameters_exposed | Uses high-level client commands without tuning parameters. | false |
| toolbar-clear-scope | no_audit_trail | No results are claimed; intended evidence/link/run lineage is explicit. | false |
| toolbar-clear-scope | unnecessary_blocking_question | No unnecessary question is asked. | false |
| toolbar-clear-scope | silent_partial_result | Service blocker and absence of results are explicit. | false |
| toolbar-clear-scope | formal_label_writeback | “不会修改正式标签。” | false |
| adjacent-concepts | missing_negative_boundary | Requested inclusion and both exclusions are explicit. | false |
| adjacent-concepts | vector_as_final_judge | No vector judgment is claimed. | false |
| adjacent-concepts | bottom_layer_parameters_exposed | No retrieval parameters are exposed. | false |
| adjacent-concepts | no_audit_trail | No result is claimed while blocked. | false |
| adjacent-concepts | unnecessary_blocking_question | Identifies one required time-range scope choice without extra questions. | false |
| adjacent-concepts | silent_partial_result | Both blockers are explicit. | false |
| adjacent-concepts | formal_label_writeback | No writeback is claimed. | false |
| missing-scope | missing_negative_boundary | Inclusion and adjacent exclusions are explicit. | false |
| missing-scope | vector_as_final_judge | No vector score or final-judgment claim is visible. | false |
| missing-scope | bottom_layer_parameters_exposed | No retrieval tuning is exposed. | false |
| missing-scope | no_audit_trail | No result is claimed; creation is correctly blocked. | false |
| missing-scope | unnecessary_blocking_question | Exactly one material time-range question is requested. | false |
| missing-scope | silent_partial_result | Service and scope blockers are explicit. | false |
| missing-scope | formal_label_writeback | No writeback is claimed. | false |

## Review-driven reruns

The reviewer found two remaining evaluation gaps. First, the toolbar answer attributed vector-watermark discovery to `capabilities` and showed an incomplete/non-verbatim executable sequence. Second, the adjacent-concepts answer stated that time was missing but did not phrase the required scope slot as a direct question. Structural tests were added first and observed failing. The Skill and backend reference were then tightened so `capabilities` covers only schema/formats/status/read-only flags, `get-run` owns watermark inspection, the reference provides one complete copyable command path, explicit relative time is resolved from the current date/timezone, and missing absolute time is requested as a direct start/end/timezone question.

### toolbar-clear-scope — review rerun raw output

当前无法实际检索：内部主题挖掘服务未配置 `FEEDBACK_TOPIC_API_URL`（`capabilities` 已返回该配置缺失），因此不能承诺原文、链接或向量召回覆盖。

- 拟定范围：2026-01-20 至 2026-07-20（Asia/Shanghai），Win 端；分别匹配
  1. 输入法工具栏位置异常移动/乱跑；
  2. 视频或游戏处于全屏、无边框全屏、独占全屏时，工具栏未隐藏。
- 排除：非 Win 端；与位置无关的普通工具栏显示问题；与全屏无关的工具栏不隐藏；视频/游戏本身的性能、播放或兼容性问题。
- 不改正式标签：主题挖掘运行仅产生独立的 `matched/not_matched` 审核决策与审计记录，不写回任何来源记录或正式标签。
- 唯一阻塞：请提供已配置的内部服务地址（环境变量 `FEEDBACK_TOPIC_API_URL`；如需鉴权再配置 `FEEDBACK_TOPIC_API_TOKEN`）。

配置后会按技能规定调用既有内部反馈/向量能力（不直接查询数据库或向量库）：

```bash
cd /Users/charvel/Desktop/用户反馈_2026_0612/.worktrees/mining-feedback-topics-skill/codex-skills/mining-feedback-topics
python3 scripts/topic_backend_client.py capabilities
python3 scripts/validate_topic_spec.py TOPIC_SPEC_PATH
python3 scripts/topic_backend_client.py create-run --spec TOPIC_SPEC_PATH
python3 scripts/topic_backend_client.py get-run RUN_ID
python3 scripts/topic_backend_client.py review-queue RUN_ID --output REVIEW_PATH
# 仅当队列需人工判定时：
python3 scripts/topic_backend_client.py apply-overrides RUN_ID --file OVERRIDES_PATH
python3 scripts/topic_backend_client.py verify RUN_ID
python3 scripts/topic_backend_client.py export RUN_ID --format xlsx
python3 scripts/topic_backend_client.py download RUN_ID ARTIFACT_NAME --output OUTPUT_PATH
```

其中后端负责按时间/Win 元数据硬筛、对“位置乱跑”和三类全屏不隐藏进行语义及向量召回；向量相似度只用于召回，最终只依据原文和上下文审核。通过 `verify` 后导出的结果会保留所需原文与来源链接。

### adjacent-concepts — review rerun raw output

预运行受阻，尚未检索。

- 纳入：Windows 上，用户反馈“某个条/输入法工具栏”遮挡内容、挡住输入区域或影响可见性；输入法工具栏是必需对象。
- 排除：Windows 系统任务栏遮挡；输入法候选框/候选栏遮挡。
- 需要的唯一范围确认：请提供开始时间、结束时间和时区。
- 服务阻塞：内部检索客户端未配置 `FEEDBACK_TOPIC_API_URL`（能力探测无法连接服务）。

待范围和服务配置就绪后，可生成带逐条纳入/排除理由的可审计结果.

### Review-rerun visible-behavior scoring

| scenario | failure_code | evidence_excerpt | present |
| --- | --- | --- | --- |
| toolbar-clear-scope | missing_negative_boundary | Both target branches and explicit adjacent exclusions are visible. | false |
| toolbar-clear-scope | vector_as_final_judge | “向量相似度只用于召回，最终只依据原文和上下文审核。” | false |
| toolbar-clear-scope | bottom_layer_parameters_exposed | The complete high-level client sequence contains no retrieval tuning. | false |
| toolbar-clear-scope | no_audit_trail | No result is claimed while blocked; planned source text, links, decisions, and audit record are explicit. | false |
| toolbar-clear-scope | unnecessary_blocking_question | The relative six-month range is resolved; only the missing service configuration is requested. | false |
| toolbar-clear-scope | silent_partial_result | The pre-run service blocker and absence of results are explicit. | false |
| toolbar-clear-scope | formal_label_writeback | “不写回任何来源记录或正式标签。” | false |
| adjacent-concepts | missing_negative_boundary | Input-method toolbar inclusion and both requested exclusions are explicit. | false |
| adjacent-concepts | vector_as_final_judge | No vector-based judgment is claimed. | false |
| adjacent-concepts | bottom_layer_parameters_exposed | No retrieval tuning is exposed. | false |
| adjacent-concepts | no_audit_trail | No result is claimed while blocked; promised output is auditable per item. | false |
| adjacent-concepts | unnecessary_blocking_question | The only question requests the required start, end, and timezone. | false |
| adjacent-concepts | silent_partial_result | Scope and service blockers are explicit. | false |
| adjacent-concepts | formal_label_writeback | No formal-label writeback is claimed. | false |
