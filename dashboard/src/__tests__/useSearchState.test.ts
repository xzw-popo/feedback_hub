import { describe, expect, it, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { useSearchState } from '@/composables/useSearchState'
import type { ConversationItem } from '@/api/feedback'

vi.mock('@/api/feedback', async () => {
  const actual = await vi.importActual<typeof import('@/api/feedback')>('@/api/feedback')
  return {
    ...actual,
    smartSearch: vi.fn(),
    keywordSearch: vi.fn(),
    fineFilterSSE: vi.fn(),
  }
})

import { smartSearch } from '@/api/feedback'

const mockedSmartSearch = vi.mocked(smartSearch)

function item(): ConversationItem {
  return {
    conversation_id: 'conv1',
    L1: 'A.Bug',
    L2: [],
    severity: 'P0',
    confidence: 0.9,
    reason: '_',
    msg_count: 1,
    first_ts_ms: 1747526400000,
    last_ts_ms: 1747526400000,
    user_vid: 'u1',
    appversion: '2.1.0',
    channel: 'wetype',
    service_vid: 10000,
    external_chat_url: 'https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=u1',
    platform: 'Win',
    preview_text: 'Windows 版本安装失败',
  }
}

describe('useSearchState', () => {
  beforeEach(() => {
    sessionStorage.clear()
    mockedSmartSearch.mockReset()
  })

  it('restores search results after the list view is recreated', async () => {
    mockedSmartSearch.mockResolvedValue({ total: 1, items: [item()] })

    const first = useSearchState()
    first.state.smartQuery = 'win2.1.0 有什么问题'
    await first.doSmartSearch({ platform: 'Win' })
    await nextTick()

    const restored = useSearchState()

    expect(restored.state.mode).toBe('smart')
    expect(restored.state.smartQuery).toBe('win2.1.0 有什么问题')
    expect(restored.state.total).toBe(1)
    expect(restored.state.items).toHaveLength(1)
    expect(restored.state.items[0].external_chat_url).toContain('serviceVid=10000')
    expect(restored.state.smartLoading).toBe(false)
  })
})
