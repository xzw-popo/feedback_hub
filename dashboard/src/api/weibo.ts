import { http } from './http'

export type WeiboBrandFocus = 'wechat' | 'doubao' | 'comparison' | 'other'
export type WeiboSentiment = 'positive' | 'neutral' | 'negative' | 'mixed'
export type WeiboRiskLevel = 'normal' | 'watch' | 'high'

export interface WeiboPostItem {
  id: string
  weibo_id: string
  url: string
  author_name: string
  author_id: string
  author_verified: boolean
  created_at_raw: string
  created_at_ms: number | null
  text: string
  pic_urls: string[]
  reposts_count: number | null
  comments_count: number | null
  attitudes_count: number | null
  keywords: string[]
  brand_focus: WeiboBrandFocus
  sentiment: WeiboSentiment
  topics: string[]
  post_type: string
  risk_level: WeiboRiskLevel
  confidence: number | null
  reason: string | null
  label_source: string
  first_seen_at: number
  last_seen_at: number
}

export interface WeiboListParams {
  from?: string
  to?: string
  q?: string
  brand_focus?: string
  sentiment?: string
  topic?: string
  post_type?: string
  risk_level?: string
  keyword?: string
  limit?: number
  offset?: number
}

export interface WeiboListResp {
  total: number
  items: WeiboPostItem[]
}

export interface WeiboTrendBucket {
  bucket: string
  counts: Record<string, number>
}

export interface WeiboCrawlRun {
  id: string
  status: string
  config_json: string
  started_at: number
  finished_at: number | null
  total_seen: number
  inserted_posts: number
  error_message: string | null
}

export interface WeiboStatsResp {
  total_posts: number
  brand_focus_counts: Record<string, number>
  sentiment_counts: Record<string, number>
  topic_counts: Record<string, number>
  risk_counts: Record<string, number>
  trend: WeiboTrendBucket[]
  latest_crawl_run: WeiboCrawlRun | null
  high_risk_posts: WeiboPostItem[]
}

function clean<T extends object>(obj: T): Partial<T> {
  const out: Partial<T> = {}
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined && v !== null && v !== '') {
      (out as Record<string, unknown>)[k] = v
    }
  }
  return out
}

export async function listWeiboPosts(params: WeiboListParams): Promise<WeiboListResp> {
  const r = await http.get<WeiboListResp>('/api/weibo/posts', { params: clean(params) })
  return r.data
}

export async function getWeiboStats(params: { from?: string; to?: string }): Promise<WeiboStatsResp> {
  const r = await http.get<WeiboStatsResp>('/api/weibo/stats', { params: clean(params) })
  return r.data
}

