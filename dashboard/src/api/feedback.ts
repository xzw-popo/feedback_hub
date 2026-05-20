import { http } from './http'
import type { Severity } from '@/constants/labels'

export interface ConversationItem {
  conversation_id: string
  L1: string
  L2: string[]
  severity: Severity
  confidence: number
  reason: string | null
  msg_count: number
  first_ts_ms: number
  last_ts_ms: number
  user_vid: string | null
  appversion: string | null
  channel: string
  preview_text: string
}

export interface MessageItem {
  feedback_id: string
  msg_seq: number
  ts_ms: number
  text: string
  appversion: string | null
  platform: string | null
  L1: string | null
  L2: string[]
  severity: Severity | null
  confidence: number | null
  reason: string | null
  source: 'rule' | 'llm' | null
  rule_name: string | null
}

export interface ListResp { total: number; items: ConversationItem[] }
export interface DetailResp { conversation: ConversationItem; messages: MessageItem[] }
export interface DistributionResp {
  L1: Record<string, number>
  L2: Record<string, number>
  severity: Record<string, number>
}
export interface TrendBucket { bucket: string; counts: Record<string, number> }
export interface TrendResp { granularity: 'day' | 'hour'; buckets: TrendBucket[] }

export interface ListParams {
  from?: string
  to?: string
  L1?: string
  L2?: string
  severity?: string
  q?: string
  limit?: number
  offset?: number
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

export async function listConversations(params: ListParams): Promise<ListResp> {
  const r = await http.get<ListResp>('/api/conversations', { params: clean(params) })
  return r.data
}

export async function getConversation(id: string): Promise<DetailResp> {
  const r = await http.get<DetailResp>(`/api/conversations/${encodeURIComponent(id)}`)
  return r.data
}

export async function getDistribution(p: { from?: string; to?: string }): Promise<DistributionResp> {
  const r = await http.get<DistributionResp>('/api/stats/distribution', { params: clean(p) })
  return r.data
}

export async function getTrend(
  p: { from?: string; to?: string; granularity?: 'day' | 'hour' },
): Promise<TrendResp> {
  const r = await http.get<TrendResp>('/api/stats/trend', { params: clean(p) })
  return r.data
}

export function exportCsvUrl(params: ListParams): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(clean(params))) {
    qs.set(k, String(v))
  }
  return `/api/export.csv${qs.toString() ? '?' + qs.toString() : ''}`
}
