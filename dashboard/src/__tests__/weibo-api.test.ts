import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/http', () => ({ http: { get: vi.fn() } }))

import { http } from '@/api/http'
import { getWeiboStats, listWeiboPosts } from '@/api/weibo'

const mockedGet = vi.mocked(http.get)

beforeEach(() => { mockedGet.mockReset() })

describe('weibo api', () => {
  it('lists posts with cleaned params', async () => {
    mockedGet.mockResolvedValueOnce({ data: { total: 0, items: [] } })

    await listWeiboPosts({ q: '豆包', brand_focus: '', limit: 20, offset: 0 })

    expect(mockedGet).toHaveBeenCalledWith('/api/weibo/posts', {
      params: { q: '豆包', limit: 20, offset: 0 },
    })
  })

  it('fetches stats with cleaned params', async () => {
    mockedGet.mockResolvedValueOnce({ data: { total_posts: 0 } })

    await getWeiboStats({ from: '2026-06-01', to: '' })

    expect(mockedGet).toHaveBeenCalledWith('/api/weibo/stats', {
      params: { from: '2026-06-01' },
    })
  })
})
