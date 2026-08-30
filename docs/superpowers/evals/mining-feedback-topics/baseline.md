# No-Skill Baseline: Mining Feedback Topics

This file preserves the three baseline-agent answers verbatim. Each agent received only the corresponding user prompt, in a fresh context, without the proposed Skill, rubric, design specification, or expected architecture.

## toolbar-clear-scope

### Prompt

找最近半年 Win 端反馈：一类是输入法工具栏位置乱跑，另一类是视频或游戏全屏、无边框全屏或独占全屏时工具栏不隐藏。要原文和链接，不要改正式标签。请说明你会如何调用现有内部反馈和向量工具完成。

### Raw baseline answer

已找到最近半年 Win 端的已审计反馈快照：2026-01-16 至 2026-07-15（快照时间 2026-07-16 13:59:50）。

- 工具栏位置乱跑：163 条
- 视频/游戏全屏不隐藏：147 条
- 共 310 条；无重复、无缺失会话链接、无非 Win 记录

以下是最近且表述明确的原文证据。

工具栏位置乱跑：

- 7/15：「游戏全屏，退出全屏后工具条不在原来位置，漂移了」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848083609950475)

- 7/14：「电脑多屏显示时 工具栏位置到处乱跑，不是固定在主屏幕的右下角」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848070723507521)

- 7/14：「工具栏没打开一个文档位置就跑了，拖到想让它待的位置，打开新的文档就又自己跑回去了。」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848100789883363)

- 7/14：「请问上次反馈的双屏工具栏位置的问题解决了吗？在第1屏设置在右下角，切换到第2屏时，工具栏不是在右下角而是镜像位置的左下角。能实现分别设置吗？」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848122264369847)

- 7/13：「启动时工具栏不在右下角，需要手动拖放，非常不方便」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848130854708339)

- 7/13：「在显示器多屏跨屏下，工具栏显示位置飘忽不定，可以设置固定显示位置」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848070722665649)

视频或游戏全屏不隐藏：

- 7/15：「包括看视频也会影响，无法隐藏，导致强迫症非常难受，要退出输入法才能看视频」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848139444706286)

- 7/15：「你好，微信输入法显示界面会显示在游戏内，无法隐藏，如何进行设置」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848139444706286)

- 7/14：「当我进行全屏游戏或全屏播放视频时，输入状态栏无法自动隐藏」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848079314817531)

- 7/13：「我在全屏看视频时输入法的工具栏为什么不能自动隐藏？他会一直显示，很影响观影感受，希望可以改进。」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848109376753666)

- 7/13：「输入法的右下角浮窗，能不能在开全屏游戏的时候自己动隐藏啊，每次都挂在那里连个关闭按钮都没有，非要我切换系统输入法就真打很无语」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848079315148793)

- 7/12：「为什么这个工具栏，看视频全屏和游戏全屏都在置顶啊，挡视线，其他输入法都不会这样的，不是输入模式就不会出现工具栏的」
  [会话链接](https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=8848079315105563)

处理方式是只读专题挖掘，不改正式标签：先限定平台为 `Win` 和半年时间窗；以“工具栏/状态栏/悬浮栏 + 位置漂移、多屏、固定失效”等，以及“视频/游戏 + 全屏、无边框、独占 + 未隐藏、置顶、遮挡”等做关键词与向量宽召回；再拼接同用户短时上下文，进行结构化语义判定。候选框位置、Windows 系统任务栏、仅询问如何设置位置、游戏黑屏/闪退/无法输入等相邻问题应排除。向量相似度只用于召回，不能直接作为命中结论。

当前主干尚未部署通用的“反馈专题 + 混合向量检索”高层接口；其能力契约仍在设计中。因此不能把现有每日话题向量代码误称为可直接检索原始反馈的工具。接口部署后，应按 `capabilities → create-run(topic_spec) → get-run → get-review-queue → apply-overrides → verify-run → export-run` 调用：由后台保持只读快照、执行关键词/BM25 与向量召回、批量语义分类和质量审计；最终只导出专题结果，不写回 L1/L2、严重度或任何正式标签。

## adjacent-concepts

### Prompt

找 Win 上‘那个条挡着内容’的反馈。输入法工具栏算，Windows 系统任务栏和输入法候选框不算。请使用内部反馈检索能力完成，并保留可审计的纳入排除依据。

### Raw baseline answer

找到 38 条 Windows 上“微信输入法工具栏/状态栏遮挡内容”的反馈。

主要场景：

- 工具栏位置复位或多屏跑位，遮挡 WPS、CAD、编辑器、文件内容或按钮。
- 网页、PS 等应用中工具栏置顶，挡住操作按钮。
- 全屏游戏/视频时工具栏仍置顶覆盖画面。
- 用户希望支持自动隐藏、关闭、透明度、记住位置或停靠任务栏。

已按口径排除 13 条：

- Windows 系统任务栏/开始/关机区域被挡。
- 输入法候选框、输入框的位置或遮挡问题。
- 工具栏被其他窗口遮挡。
- 仅说“置顶/碍眼”，未明确遮挡内容。

完整可审计记录（含原始库、复现 SQL、纳入/排除 ID 与理由）：

[toolbar_obstruction_audit_20260720.md](/Users/charvel/Desktop/用户反馈_2026_0612/feedback_hub/data/win_toolbar_feedback_20260716/toolbar_obstruction_audit_20260720.md)

## missing-scope

### Prompt

帮我找语音输入结束后文字不上屏的反馈，使用内部反馈和向量检索能力。

### Raw baseline answer

找到了。这是一组明显的 Windows PC 端版本级回归反馈：

- 表现：语音输入结束、尤其松开 `ALT` 后，识别文字不再写入当前已聚焦的输入框，而是弹出“已复制，粘贴即可使用”，用户必须手动 `Ctrl+V`。
- 范围：高度集中在 Windows `2.1.0.27`，主要发生于 6 月 19 日至 24 日；至少 14 条反馈、12 个独立会话（同会话连续追问已去重）。
- 推断：更像“语音结束时焦点丢失，直接上屏失败后回退到剪贴板”的回归，而不是识别能力问题。

关键反馈证据：

- 6 月 19 日：用户称语音输入结束后显示“已复制”，不能直接进入当前输入框（`6a349c7c…`）。
- 6 月 19 日：右 `ALT` 语音输入过去可直接发送，现进入剪贴板，需手动 `Ctrl+V`（`6a350907…`）。
- 6 月 20 日：微信、Discord、网页输入框和 Windows 搜索栏均复现；即使光标已激活，文字仍只进入剪贴板（`6a36121d…`）。
- 6 月 20 日：用户明确描述“过去松开 ALT 后会写入有 focus 的文本框”，现在仅提示转写结果已放剪贴板（`6a362cb9…`）。
- 6 月 22 日：用户指出“只要激活语音输入，文本框就失去焦点”（`6a389228…`）。
- 6 月 22 日：电脑端语音无法直接转写到文本框，只会自动复制到剪贴板（`6a390882…`）。
- 6 月 23 日：多名用户反馈 PC 版语音转文字后不能显示在输入框，仅提示“已复制，请粘贴使用”（`6a3a3c3c…`、`6a3a3cb7…`、`6a3a3d33…`）。
- 6 月 23 日：用户称“最新版本的所有语音输入都要复制粘贴”（`6a3a7826…`）。
- 6 月 24 日：按住快捷键说话、松开后显示已复制，未直接写入输入框（`6a3b4411…`）。

浏览器场景同样存在：6 月 21 日网页光标存在仍无法转写（`6a37e83b…`）；6 月 23 日 Tabit 浏览器无法直接调入输入框（`6a3a99f5…`）。

建议将该问题归类为：`Windows 2.1.0.27 / 语音输入 / 结束上屏失败 / 焦点丢失 / 剪贴板回退`。

## Visible-behavior scoring

Only observable transcript behavior is scored; an unlinked local artifact is not treated as transcript-visible source evidence.

| scenario | failure_code | evidence_excerpt | present |
| --- | --- | --- | --- |
| toolbar-clear-scope | missing_negative_boundary | “候选框位置、Windows 系统任务栏…应排除。” | false |
| toolbar-clear-scope | vector_as_final_judge | “向量相似度只用于召回，不能直接作为命中结论。” | false |
| toolbar-clear-scope | bottom_layer_parameters_exposed | Names only high-level workflow calls; no embedding/index thresholds or backend tuning parameters are exposed. | false |
| toolbar-clear-scope | no_audit_trail | Provides quoted source evidence, session links, counts, and stated exclusions. | false |
| toolbar-clear-scope | unnecessary_blocking_question | No question was asked. | false |
| toolbar-clear-scope | silent_partial_result | Reports the snapshot window and aggregate coverage. | false |
| toolbar-clear-scope | formal_label_writeback | “不改正式标签” and “不写回 L1/L2、严重度或任何正式标签。” | false |
| adjacent-concepts | missing_negative_boundary | Explicitly excludes “Windows 系统任务栏” and “输入法候选框”. | false |
| adjacent-concepts | vector_as_final_judge | Does not state that vector similarity is a final judge. | false |
| adjacent-concepts | bottom_layer_parameters_exposed | No low-level retrieval parameters are exposed. | false |
| adjacent-concepts | no_audit_trail | Links an audit record and describes “复现 SQL、纳入/排除 ID 与理由”. | false |
| adjacent-concepts | unnecessary_blocking_question | No question was asked. | false |
| adjacent-concepts | silent_partial_result | Gives 38 included and 13 excluded; no visible indication of an omitted subset. | false |
| adjacent-concepts | formal_label_writeback | No label writeback is claimed. | false |
| missing-scope | missing_negative_boundary | The answer defines the apparent cluster but gives no explicit excluded adjacent concepts or negative boundary. | true |
| missing-scope | vector_as_final_judge | No vector score or final-judgment claim is visible. | false |
| missing-scope | bottom_layer_parameters_exposed | No low-level retrieval parameters are exposed. | false |
| missing-scope | no_audit_trail | Evidence is abbreviated IDs only; no source links, query/run record, or inclusion/exclusion basis is supplied. | true |
| missing-scope | unnecessary_blocking_question | No question was asked. | false |
| missing-scope | silent_partial_result | “至少 14 条” is presented without a data coverage boundary or unresolved-result disclosure. | true |
| missing-scope | formal_label_writeback | “建议将该问题归类为” is a recommendation, not a claimed formal-label writeback. | false |
