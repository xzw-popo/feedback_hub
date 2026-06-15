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
  platform: string
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
  platform?: string
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

// ---------------------------------------------------------------------------
// 智能搜索类型与 API
// ---------------------------------------------------------------------------

export interface MetadataFilters {
  from?: string
  to?: string
  L1?: string
  L2?: string
  severity?: string
  platform?: string
}

export interface KeywordGroup {
  keywords: string[]
  logic: 'AND' | 'OR'
}

export interface KeywordSearchRequest {
  groups: KeywordGroup[]
  excludes: string[]
  filters: MetadataFilters
  limit: number
  offset: number
}

export interface SmartSearchRequest {
  query: string
  filters: MetadataFilters
  limit: number
  offset: number
}

export interface SmartSearchResp {
  total: number
  items: ConversationItem[]
  debug?: {
    regex_groups: { patterns: string[]; logic: string }[]
    group_logic: string
    regex_patterns: string[]  // 向后兼容（展平）
    logic?: string            // 向后兼容
  }
}

export interface FineFilterBatchResult {
  id: string
  score: 1 | 2 | 3
  reason: string
}

export interface FineFilterRequest {
  query: string
  conversation_ids: string[]
  batch_size?: number  // 可选，后端会自适应（≤50条时 batch=1）
}

/** 关键词构建器搜索 */
export async function keywordSearch(req: KeywordSearchRequest): Promise<ListResp> {
  const r = await http.post<ListResp>('/api/keyword-search', req)
  return r.data
}

/** AI 智能搜索 */
export async function smartSearch(req: SmartSearchRequest): Promise<SmartSearchResp> {
  const r = await http.post<SmartSearchResp>('/api/smart-search', req)
  return r.data
}

/** AI 精筛 — 返回伪 EventSource 实例，调用方监听事件 */
export function fineFilterSSE(
  req: FineFilterRequest,
  onBatch: (results: FineFilterBatchResult[], processed: number) => void,
  onDone: (totalProcessed: number) => void,
  onError: (err: Event) => void,
  onWarning?: (message: string) => void,
): EventSource {
  // 精筛用 POST，但 EventSource 只支持 GET，因此用 fetch + 读流
  // 改用 fetch-based SSE 解析
  const controller = new AbortController()
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''

  // 用伪 EventSource 对象封装 fetch SSE
  const fakeES = {} as EventSource

  ;(async () => {
    try {
      const resp = await fetch(`${baseURL}/api/fine-filter`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
        signal: controller.signal,
      })
      if (!resp.ok) {
        onError(new Event(`HTTP ${resp.status}`))
        return
      }
      const reader = resp.body?.getReader()
      if (!reader) {
        onError(new Event('No readable stream'))
        return
      }
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // 解析 SSE 格式
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        let eventType = ''
        let data = ''
        for (const line of lines) {
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            data = line.slice(5).trim()
          } else if (!line.trim() && data) {
            // 空行 = 事件结束
            try {
              const parsed = JSON.parse(data)
              if (eventType === 'batch' || parsed.batch) {
                onBatch(parsed.batch || [], parsed.processed || 0)
              } else if (eventType === 'warning' || parsed.message) {
                onWarning?.(parsed.message || '精筛部分超时')
              } else if (eventType === 'done' || parsed.done) {
                onDone(parsed.total_processed || 0)
              }
            } catch {
              // 忽略解析错误
            }
            eventType = ''
            data = ''
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        onError(e as Event)
      }
    }
  })()

  // 返回带 abort 能力的伪 EventSource
  return {
    close() { controller.abort() },
  } as unknown as EventSource
}
