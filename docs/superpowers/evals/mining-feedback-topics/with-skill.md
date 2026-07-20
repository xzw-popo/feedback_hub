# With-Skill Evaluation: Mining Feedback Topics

All agents received the Skill by absolute path in a fresh context. They were not given the rubric, baseline failures, or expected answer. No topic service URL was configured, so forward tests could not mutate a live service.

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
