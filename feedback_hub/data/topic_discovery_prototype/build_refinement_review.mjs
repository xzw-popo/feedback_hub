import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = "/Users/charvel/Desktop/用户反馈_2026_0612";
const dataDir = path.join(
  projectRoot,
  "feedback_hub/data/topic_discovery_prototype/refinement_20260712_20260713",
);
const outputDir = path.join(
  projectRoot,
  "outputs/topic_boundary_refinement_20260712_20260713",
);
const outputPath = path.join(outputDir, "topic_boundary_refinement_review.xlsx");
const previewDir = path.join(outputDir, "_preview");

async function readJsonl(name) {
  const text = await fs.readFile(path.join(dataDir, name), "utf8");
  return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
}

const [targets, revised, audit, pool, full, store] = await Promise.all([
  readJsonl("targeted_topics.jsonl"),
  readJsonl("revised_decisions.jsonl"),
  readJsonl("boundary_audit.jsonl"),
  readJsonl("low_information_pool.jsonl"),
  readJsonl("full_overlaid_decisions.jsonl"),
  readJsonl("topic_store.jsonl"),
]);

const revisedById = new Map(revised.map((row) => [row.daily_topic_id, row]));
const auditById = new Map(audit.map((row) => [row.daily_topic_id, row]));
const colors = {
  ink: "#18302B",
  muted: "#5E6D68",
  teal: "#0F766E",
  tealLight: "#D8F0EA",
  blue: "#2563A6",
  blueLight: "#E6F0FA",
  amber: "#B7791F",
  amberLight: "#FFF2D6",
  red: "#B64242",
  redLight: "#FBE4E4",
  gray: "#E6EBE9",
  grayLight: "#F5F7F6",
  white: "#FFFFFF",
};

function firstKey(counts = {}) {
  return Object.entries(counts)
    .sort((a, b) => Number(b[1]) - Number(a[1]) || String(a[0]).localeCompare(String(b[0])))
    .map(([key]) => key)[0] || "";
}

function firstLink(topic = {}) {
  if ((topic.evidence_links || []).length) return String(topic.evidence_links[0]);
  for (const member of topic.members || []) {
    if ((member.evidence_links || []).length) return String(member.evidence_links[0]);
  }
  return "";
}

function hasMedia(topic = {}) {
  return Boolean(topic.has_media_evidence) || (topic.members || []).some(
    (member) => Boolean(member.has_media_evidence),
  );
}

function headerStyle(range) {
  range.format = {
    fill: colors.ink,
    font: { bold: true, color: colors.white, size: 10 },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#52625D" },
  };
  range.format.rowHeight = 30;
}

function bodyStyle(range) {
  range.format = {
    font: { color: colors.ink, size: 10 },
    verticalAlignment: "top",
    wrapText: true,
    borders: {
      insideHorizontal: { style: "thin", color: colors.gray },
    },
  };
}

function addTable(sheet, name, rangeAddress) {
  const table = sheet.tables.add(rangeAddress, true, name);
  table.style = "TableStyleMedium2";
  table.showBandedRows = true;
  table.showFilterButton = true;
  return table;
}

const workbook = Workbook.create();
const overview = workbook.worksheets.add("结果概览");
const comparison = workbook.worksheets.add("新旧对照");
const lowInfo = workbook.worksheets.add("低信息池");
const auditSheet = workbook.worksheets.add("边界审计");
const policy = workbook.worksheets.add("口径说明");

for (const sheet of [overview, comparison, lowInfo, auditSheet, policy]) {
  sheet.showGridLines = false;
}

// 新旧对照
const comparisonHeaders = [
  "daily_topic_id", "当前主题", "入选风险", "旧判定", "旧历史主题", "新判定",
  "新历史主题", "是否变化", "新置信度", "模型理由", "审计标记", "平台",
  "功能候选", "反馈类型", "有媒体", "原反馈链接", "人工结论", "人工备注",
];
const comparisonRows = targets.map((target, index) => {
  const current = target.daily_topic || {};
  const oldDecision = target.baseline_decision || {};
  const newDecision = revisedById.get(target.daily_topic_id) || {};
  const auditRow = auditById.get(target.daily_topic_id) || {};
  const excelRow = index + 2;
  return [
    target.daily_topic_id,
    current.title || "",
    (target.selection_reasons || []).join(" | "),
    oldDecision.verdict || "",
    oldDecision.historical_topic_id || "",
    newDecision.verdict || "",
    newDecision.historical_topic_id || "",
    null,
    Number(newDecision.confidence || 0),
    newDecision.reason || "",
    (auditRow.audit_flags || []).join(" | "),
    firstKey(current.platform_counts),
    firstKey(current.feature_candidate_counts),
    firstKey(current.feedback_type_counts),
    hasMedia(current) ? "是" : "否",
    firstLink(current),
    "",
    "",
  ];
});
comparison.getRangeByIndexes(0, 0, 1, comparisonHeaders.length).values = [comparisonHeaders];
comparison.getRangeByIndexes(1, 0, comparisonRows.length, comparisonHeaders.length).values = comparisonRows;
for (let row = 2; row <= comparisonRows.length + 1; row += 1) {
  comparison.getRange(`H${row}`).formulas = [[`=IF(OR(D${row}<>F${row},E${row}<>G${row}),"是","否")`]];
}
headerStyle(comparison.getRange(`A1:R1`));
bodyStyle(comparison.getRange(`A2:R${comparisonRows.length + 1}`));
comparison.getRange(`A2:R${comparisonRows.length + 1}`).format.rowHeight = 44;
comparison.getRange(`I2:I${comparisonRows.length + 1}`).format.numberFormat = "0%";
comparison.getRange(`A2:A${comparisonRows.length + 1}`).format.numberFormat = "@";
comparison.getRange(`P2:P${comparisonRows.length + 1}`).format.font = { color: colors.blue, size: 10 };
comparison.getRange(`Q2:Q${comparisonRows.length + 1}`).dataValidation = {
  rule: { type: "list", values: ["接受新结果", "保留旧结果", "需继续讨论", "信息不足"] },
};
comparison.getRange(`H2:H${comparisonRows.length + 1}`).conditionalFormats.add("containsText", {
  text: "是", format: { fill: colors.amberLight, font: { color: colors.amber, bold: true } },
});
comparison.getRange(`F2:F${comparisonRows.length + 1}`).conditionalFormats.add("containsText", {
  text: "low_information", format: { fill: colors.gray, font: { color: colors.muted } },
});
comparison.getRange(`K2:K${comparisonRows.length + 1}`).conditionalFormats.add("notContainsBlanks", {
  format: { fill: colors.redLight, font: { color: colors.red } },
});
addTable(comparison, "ComparisonTable", `A1:R${comparisonRows.length + 1}`);
comparison.freezePanes.freezeRows(1);
comparison.freezePanes.freezeColumns(2);
const comparisonWidths = [23, 34, 25, 18, 19, 18, 19, 10, 10, 46, 31, 12, 22, 20, 10, 45, 16, 34];
comparisonWidths.forEach((width, index) => {
  comparison.getRangeByIndexes(0, index, comparisonRows.length + 1, 1).format.columnWidth = width;
});

// 低信息池
const poolHeaders = [
  "daily_topic_id", "主题", "模型理由", "置信度", "平台", "功能候选",
  "会话数", "有媒体", "进入媒体附录", "原反馈链接", "人工处理", "人工备注",
];
const poolRows = pool.map((row) => [
  row.daily_topic_id,
  row.title || "",
  row.decision_reason || "",
  Number(row.decision_confidence || 0),
  firstKey(row.platform_counts),
  firstKey(row.feature_candidate_counts),
  Number(row.conversation_count || (row.conversation_ids || []).length || 0),
  row.has_media_evidence ? "是" : "否",
  row.media_appendix_eligible ? "是" : "否",
  firstLink(row),
  "",
  "",
]);
lowInfo.getRangeByIndexes(0, 0, 1, poolHeaders.length).values = [poolHeaders];
lowInfo.getRangeByIndexes(1, 0, poolRows.length, poolHeaders.length).values = poolRows;
headerStyle(lowInfo.getRange("A1:L1"));
bodyStyle(lowInfo.getRange(`A2:L${poolRows.length + 1}`));
lowInfo.getRange(`A2:L${poolRows.length + 1}`).format.rowHeight = 44;
lowInfo.getRange(`D2:D${poolRows.length + 1}`).format.numberFormat = "0%";
lowInfo.getRange(`J2:J${poolRows.length + 1}`).format.font = { color: colors.blue, size: 10 };
lowInfo.getRange(`K2:K${poolRows.length + 1}`).dataValidation = {
  rule: { type: "list", values: ["保留低信息池", "升级稳定主题", "进入媒体附录", "不进入报告"] },
};
lowInfo.getRange(`H2:I${poolRows.length + 1}`).conditionalFormats.add("containsText", {
  text: "是", format: { fill: colors.amberLight, font: { color: colors.amber, bold: true } },
});
addTable(lowInfo, "LowInformationTable", `A1:L${poolRows.length + 1}`);
lowInfo.freezePanes.freezeRows(1);
const poolWidths = [23, 38, 48, 10, 12, 22, 10, 10, 14, 45, 18, 34];
poolWidths.forEach((width, index) => lowInfo.getRangeByIndexes(0, index, poolRows.length + 1, 1).format.columnWidth = width);

// 边界审计
const auditHeaders = [
  "daily_topic_id", "当前主题", "判定", "选择历史主题", "历史主题标题",
  "相似度", "审计标记", "有媒体", "原反馈链接", "人工结论", "人工备注",
  "高相似新主题", "多对一同主题", "低置信同主题", "跨平台风险", "泛操作风险", "低信息媒体",
];
const auditRows = audit.map((row) => {
  const flags = new Set(row.audit_flags || []);
  return [
    row.daily_topic_id,
    row.current_title || "",
    row.verdict || "",
    row.selected_historical_topic_id || "",
    row.historical_title || "",
    Number(row.selected_similarity || 0),
    (row.audit_flags || []).join(" | "),
    row.has_media_evidence ? "是" : "否",
    row.evidence_link || "",
    "",
    "",
    flags.has("high_similarity_new_topic") ? 1 : 0,
    flags.has("many_to_one_same_topic") ? 1 : 0,
    flags.has("low_confidence_same_topic") ? 1 : 0,
    flags.has("platform_boundary_risk") ? 1 : 0,
    flags.has("generic_operation_boundary_risk") ? 1 : 0,
    flags.has("low_information_media") ? 1 : 0,
  ];
});
auditSheet.getRangeByIndexes(0, 0, 1, auditHeaders.length).values = [auditHeaders];
auditSheet.getRangeByIndexes(1, 0, auditRows.length, auditHeaders.length).values = auditRows;
headerStyle(auditSheet.getRange("A1:Q1"));
bodyStyle(auditSheet.getRange(`A2:Q${auditRows.length + 1}`));
auditSheet.getRange(`A2:Q${auditRows.length + 1}`).format.rowHeight = 44;
auditSheet.getRange(`F2:F${auditRows.length + 1}`).format.numberFormat = "0%";
auditSheet.getRange(`I2:I${auditRows.length + 1}`).format.font = { color: colors.blue, size: 10 };
auditSheet.getRange(`J2:J${auditRows.length + 1}`).dataValidation = {
  rule: { type: "list", values: ["无风险", "需改判", "需人工确认", "保留观察"] },
};
auditSheet.getRange(`G2:G${auditRows.length + 1}`).conditionalFormats.add("notContainsBlanks", {
  format: { fill: colors.redLight, font: { color: colors.red } },
});
auditSheet.getRange(`L2:Q${auditRows.length + 1}`).format.numberFormat = "0";
addTable(auditSheet, "BoundaryAuditTable", `A1:Q${auditRows.length + 1}`);
auditSheet.freezePanes.freezeRows(1);
auditSheet.freezePanes.freezeColumns(2);
const auditWidths = [23, 38, 18, 19, 38, 10, 34, 10, 45, 18, 34, 12, 12, 12, 12, 12, 12];
auditWidths.forEach((width, index) => auditSheet.getRangeByIndexes(0, index, auditRows.length + 1, 1).format.columnWidth = width);

// 口径说明
const policyRows = [
  ["主题记忆资格", "low_information", "文本不足以识别产品对象、请求/症状、触发或期望；保留证据，但不创建稳定 topic_id。"],
  ["同主题", "same_topic", "必须是同一产品对象或能力，并可由同一排查、修复或产品决策处理。"],
  ["新主题", "new_topic", "证据足以形成可操作主题，但召回候选均不描述同一问题。"],
  ["可能子主题", "possible_subtopic", "同一对象下出现可独立处理的症状、场景、平台变体或产品选项。"],
  ["不确定", "uncertain", "存在一个合理候选，但现有证据不足以消除边界歧义。"],
  ["泛操作边界", "审计规则", "排序、大小、开关、入口、显示、同步、自定义等共同操作不能跨不同对象建立主题关系。"],
  ["Bug 平台边界", "审计规则", "Bug 默认按平台或机制区分；仅在证据支持同一排查和修复时跨平台合并。"],
  ["需求平台边界", "审计规则", "功能需求可在多个平台对应同一个产品决策时合并，平台作为报告属性保留。"],
  ["媒体低信息", "报告去向", "文本无法形成主题但有图片/视频证据时，进入独立媒体附录，不与正文主题混排。"],
  ["审计层", "行为", "审计只产生风险标记，不自动改写模型判定。"],
];
policy.getRange("A1:C1").merge();
policy.getRange("A1").values = [["动态主题记忆与边界判定口径"]];
policy.getRange("A1:C1").format = {
  fill: colors.ink, font: { bold: true, color: colors.white, size: 16 },
  verticalAlignment: "center",
};
policy.getRange("A1:C1").format.rowHeight = 38;
policy.getRange("A3:C3").values = [["规则维度", "标准值", "说明"]];
policy.getRangeByIndexes(3, 0, policyRows.length, 3).values = policyRows;
headerStyle(policy.getRange("A3:C3"));
bodyStyle(policy.getRange(`A4:C${policyRows.length + 3}`));
policy.getRange(`A4:C${policyRows.length + 3}`).format.rowHeight = 30;
policy.getRange(`A4:A${policyRows.length + 3}`).format.font = { bold: true, color: colors.ink, size: 10 };
policy.getRange(`B4:B${policyRows.length + 3}`).format.fill = colors.tealLight;
policy.getRange("A:A").format.columnWidth = 22;
policy.getRange("B:B").format.columnWidth = 22;
policy.getRange("C:C").format.columnWidth = 76;
policy.freezePanes.freezeRows(3);

// 结果概览：公式直接引用明细表。
overview.getRange("A1:L2").merge();
overview.getRange("A1").values = [["7月13日动态主题边界复核"]];
overview.getRange("A1:L2").format = {
  fill: colors.ink,
  font: { bold: true, color: colors.white, size: 18 },
  verticalAlignment: "center",
};
overview.getRange("A3:L3").merge();
overview.getRange("A3").values = [["针对 7月12日 → 7月13日生命周期结果的定向实验 | 169 个高风险主题"]];
overview.getRange("A3:L3").format = {
  fill: colors.grayLight, font: { color: colors.muted, size: 10 },
  verticalAlignment: "center",
};

const kpis = [
  ["目标主题", `=COUNTA('新旧对照'!$A$2:$A$${comparisonRows.length + 1})`, colors.blueLight],
  ["发生变化", `=COUNTIF('新旧对照'!$H$2:$H$${comparisonRows.length + 1},"是")`, colors.amberLight],
  ["低信息池", `=COUNTA('低信息池'!$A$2:$A$${poolRows.length + 1})`, colors.gray],
  ["审计主题", `=COUNTA('边界审计'!$A$2:$A$${auditRows.length + 1})`, colors.redLight],
  ["全量决策", full.length, colors.tealLight],
  ["稳定主题库", store.length, colors.tealLight],
];
kpis.forEach(([label, formulaOrValue, fill], index) => {
  const col = index * 2 + 1;
  const labelCell = overview.getCell(4, col - 1);
  const valueCell = overview.getCell(5, col - 1);
  labelCell.values = [[label]];
  if (typeof formulaOrValue === "string" && formulaOrValue.startsWith("=")) {
    valueCell.formulas = [[formulaOrValue]];
  } else {
    valueCell.values = [[formulaOrValue]];
  }
  overview.getRangeByIndexes(4, col - 1, 2, 2).format = {
    fill,
    borders: { preset: "outside", style: "thin", color: colors.gray },
    verticalAlignment: "center",
  };
  overview.getRangeByIndexes(4, col - 1, 1, 2).format.font = {
    color: colors.muted, size: 10,
  };
  overview.getRangeByIndexes(5, col - 1, 1, 2).format.font = {
    bold: true, color: colors.ink, size: 18,
  };
});

overview.getRange("A8:C8").values = [["判定", "旧结果", "新结果"]];
const verdicts = ["same_topic", "new_topic", "possible_subtopic", "uncertain", "low_information"];
overview.getRangeByIndexes(8, 0, verdicts.length, 1).values = verdicts.map((value) => [value]);
verdicts.forEach((_verdict, index) => {
  const row = index + 9;
  overview.getRange(`B${row}`).formulas = [[`=COUNTIF('新旧对照'!$D$2:$D$${comparisonRows.length + 1},A${row})`]];
  overview.getRange(`C${row}`).formulas = [[`=COUNTIF('新旧对照'!$F$2:$F$${comparisonRows.length + 1},A${row})`]];
});
headerStyle(overview.getRange("A8:C8"));
bodyStyle(overview.getRange("A9:C13"));
overview.getRange("B9:C13").format.numberFormat = "#,##0";
const verdictChart = overview.charts.add("bar", overview.getRange("A8:C13"));
verdictChart.title = "高风险样本：新旧判定分布";
verdictChart.hasLegend = true;
verdictChart.yAxis = { numberFormatCode: "#,##0" };
verdictChart.setPosition("E8", "L21");

const auditFlags = [
  "high_similarity_new_topic", "many_to_one_same_topic", "low_confidence_same_topic",
  "platform_boundary_risk", "generic_operation_boundary_risk", "low_information_media",
];
overview.getRange("A16:B16").values = [["审计标记", "数量"]];
overview.getRangeByIndexes(16, 0, auditFlags.length, 1).values = auditFlags.map((value) => [value]);
auditFlags.forEach((_flag, index) => {
  const row = index + 17;
  const sourceColumn = String.fromCharCode("L".charCodeAt(0) + index);
  overview.getRange(`B${row}`).formulas = [[`=SUM('边界审计'!$${sourceColumn}$2:$${sourceColumn}$${auditRows.length + 1})`]];
});
headerStyle(overview.getRange("A16:B16"));
bodyStyle(overview.getRange("A17:B22"));
overview.getRange("A25:L25").merge();
overview.getRange("A25").values = [["阅读建议：先看“新旧对照”的变化项，再 review“边界审计”；低信息且带媒体的反馈单独查看“低信息池”。"]];
overview.getRange("A25:L25").format = {
  fill: colors.grayLight, font: { color: colors.muted, italic: true, size: 10 },
  wrapText: true, verticalAlignment: "center",
};
overview.getRange("A:A").format.columnWidth = 31;
overview.getRange("B:C").format.columnWidth = 14;
overview.getRange("D:D").format.columnWidth = 4;
overview.getRange("E:L").format.columnWidth = 13;
overview.getRange("A1:L25").format.font.name = "Aptos";

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const checks = [];
for (const [sheetName, range] of [
  ["结果概览", "A1:L25"],
  ["新旧对照", "A1:R12"],
  ["低信息池", `A1:L${Math.min(poolRows.length + 1, 12)}`],
  ["边界审计", "A1:K12"],
  ["口径说明", `A1:C${policyRows.length + 3}`],
]) {
  const rendered = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  const previewPath = path.join(previewDir, `${sheetName}.png`);
  await fs.writeFile(previewPath, new Uint8Array(await rendered.arrayBuffer()));
  checks.push({ sheetName, previewPath });
}

const inspect = await workbook.inspect({
  kind: "table",
  range: "结果概览!A1:L25",
  include: "values,formulas",
  tableMaxRows: 25,
  tableMaxCols: 12,
  maxChars: 12000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const reopenedCheck = await reopened.inspect({
  kind: "table",
  range: "新旧对照!A1:R4",
  include: "values,formulas",
  tableMaxRows: 4,
  tableMaxCols: 18,
  maxChars: 8000,
});
console.log(JSON.stringify({
  outputPath,
  checks,
  inspect: inspect.ndjson,
  errors: errors.ndjson,
  reopenedCheck: reopenedCheck.ndjson,
}));
