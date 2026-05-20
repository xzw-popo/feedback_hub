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
