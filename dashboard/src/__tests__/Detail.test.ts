import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Detail from '@/views/Detail.vue'
import type { DetailResp } from '@/api/feedback'

vi.mock('@/api/feedback', () => ({ getConversation: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn() } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import { getConversation } from '@/api/feedback'

const mockedGetConversation = vi.mocked(getConversation)

function detailPayload(): DetailResp {
  return {
    conversation: {
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
      appversion: '1.0',
      channel: 'wetype',
      service_vid: 10000,
      external_chat_url: 'https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=u1',
      platform: 'iOS',
      preview_text: '闪退了',
    },
    messages: [{
      feedback_id: 'fb1',
      msg_seq: 0,
      ts_ms: 1747526400000,
      text: '闪退了',
      appversion: '1.0',
      platform: 'iOS',
      L1: 'A.Bug',
      L2: [],
      severity: 'P0',
      confidence: 0.9,
      reason: '_',
      source: 'rule',
      rule_name: '_',
    }],
  }
}

describe('Detail', () => {
  beforeEach(() => {
    mockedGetConversation.mockResolvedValue(detailPayload())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('opens the original chat url from the header action', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mount(Detail, {
      props: { id: 'conv1' },
      global: {
        directives: {
          loading: {},
        },
        stubs: {
          L1Tag: true,
          SeverityTag: true,
          ElButton: { template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>', props: ['disabled'] },
          ElDivider: true,
        },
      },
    })

    await flushPromises()
    await wrapper.findAll('button')[1].trigger('click')

    expect(open).toHaveBeenCalledWith(
      'https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=u1',
      '_blank',
      'noopener,noreferrer',
    )
  })
})
