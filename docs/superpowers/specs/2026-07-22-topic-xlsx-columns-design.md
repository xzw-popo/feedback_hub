# Topic Feedback XLSX Column Design

## Goal

Make the final user-facing feedback workbook easier to read by removing internal audit columns from the `反馈清单` worksheet and placing reviewer rationale at the end.

## Scope

Only the XLSX `反馈清单` worksheet changes. JSONL artifacts, the quality report, the `导出元数据` worksheet, verification inputs, and persisted run artifacts keep their existing fields.

## Column Contract

The worksheet columns, in order, are:

1. `反馈时间`
2. `反馈原文`
3. `对应链接`
4. `平台`
5. `版本`
6. `设备`
7. `Feedback ID`
8. `判定理由`
9. `证据`

The worksheet no longer contains `命中分类`, `Conversation ID`, `Run ID`, or `数据截止时间`.

The corresponding row values, hyperlink column, auto-filter range, and column widths must follow the new nine-column contract. `判定理由` and `证据` remain available as the last two columns.

## Compatibility

- `final_results.jsonl` retains labels, run identity, conversation identity, and data-cutoff fields.
- `quality_report.json` retains run and cutoff information.
- The `导出元数据` worksheet retains its existing aggregate scope fields.
- Existing verified runs can be exported with the new presentation format without changing their underlying artifacts.

## Verification

An export regression test will assert the complete header sequence, absence of the four removed headers, hyperlink placement in the new third column, and preservation of audit fields in JSONL and the quality report. The end-to-end test will stop expecting data cutoff in the worksheet while continuing to verify it in machine-readable artifacts.
