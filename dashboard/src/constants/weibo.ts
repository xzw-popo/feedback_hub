export const BRAND_FOCUS_LABEL: Record<string, string> = {
  wechat: '微信',
  doubao: '豆包',
  comparison: '对比',
  other: '其他',
}

export const SENTIMENT_LABEL: Record<string, string> = {
  positive: '正向',
  neutral: '中性',
  negative: '负向',
  mixed: '混合',
}

export const RISK_LEVEL_LABEL: Record<string, string> = {
  normal: '普通',
  watch: '值得关注',
  high: '高风险',
}

export const TOPIC_LABEL: Record<string, string> = {
  input_experience: '输入体验',
  ai_capability: 'AI能力',
  privacy: '隐私安全',
  ads: '广告打扰',
  migration_intent: '迁移意愿',
  feature_comparison: '功能对比',
  brand_perception: '品牌认知',
  bug: '故障问题',
  other: '其他',
}

export const BRAND_FOCUS_OPTIONS = [
  { label: '全部品牌焦点', value: '' },
  { label: '微信', value: 'wechat' },
  { label: '豆包', value: 'doubao' },
  { label: '微信与豆包对比', value: 'comparison' },
  { label: '其他', value: 'other' },
]

export const SENTIMENT_OPTIONS = [
  { label: '全部情绪', value: '' },
  { label: '正向', value: 'positive' },
  { label: '中性', value: 'neutral' },
  { label: '负向', value: 'negative' },
  { label: '混合', value: 'mixed' },
]

export const RISK_LEVEL_OPTIONS = [
  { label: '全部风险', value: '' },
  { label: '普通', value: 'normal' },
  { label: '值得关注', value: 'watch' },
  { label: '高风险', value: 'high' },
]

export function labelFor(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—'
  return map[value] ?? value
}

