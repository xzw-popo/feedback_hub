import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/http', () => ({ http: { get: vi.fn() } }))

import { http } from '@/api/http'
import {
  listConversations, getConversation, getDistribution, getTrend, exportCsvUrl,
} from '@/api/feedback'

const mockedGet = vi.mocked(http.get)
beforeEach(() => { mockedGet.mockReset() })

describe('listConversations', () => {
  it('omits empty params', async () => {
    mockedGet.mockResolvedValue({ data: { total: 0, items: [] } })
    await listConversations({ L1: '', q: undefined, limit: 50 })
    expect(mockedGet).toHaveBeenCalledWith('/api/conversations', { params: { limit: 50 } })
  })
  it('passes through filled params', async () => {
    mockedGet.mockResolvedValue({ data: { total: 1, items: [] } })
    await listConversations({ L1: 'A.Bug', from: '2026-05-15', to: '2026-05-21' })
    expect(mockedGet).toHaveBeenCalledWith('/api/conversations', {
      params: { L1: 'A.Bug', from: '2026-05-15', to: '2026-05-21' },
    })
  })
})

describe('getConversation', () => {
  it('encodes id in path', async () => {
    mockedGet.mockResolvedValue({ data: { conversation: {} as never, messages: [] } })
    await getConversation('abc 123')
    expect(mockedGet).toHaveBeenCalledWith('/api/conversations/abc%20123')
  })
})

describe('getDistribution', () => {
  it('passes from/to', async () => {
    mockedGet.mockResolvedValue({ data: { L1: {}, L2: {}, severity: {} } })
    await getDistribution({ from: '2026-05-15', to: '2026-05-21' })
    expect(mockedGet).toHaveBeenCalledWith('/api/stats/distribution', {
      params: { from: '2026-05-15', to: '2026-05-21' },
    })
  })
})

describe('getTrend', () => {
  it('passes granularity', async () => {
    mockedGet.mockResolvedValue({ data: { granularity: 'day', buckets: [] } })
    await getTrend({ granularity: 'day' })
    expect(mockedGet).toHaveBeenCalledWith('/api/stats/trend', { params: { granularity: 'day' } })
  })
})

describe('exportCsvUrl', () => {
  it('builds url with query string', () => {
    const url = exportCsvUrl({ L1: 'A.Bug', from: '2026-05-15' })
    expect(url).toContain('/api/export.csv?')
    expect(url).toContain('L1=A.Bug')
    expect(url).toContain('from=2026-05-15')
  })
  it('returns plain path when no params', () => {
    expect(exportCsvUrl({})).toBe('/api/export.csv')
  })
})
