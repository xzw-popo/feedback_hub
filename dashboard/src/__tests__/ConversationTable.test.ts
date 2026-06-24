import { describe, expect, it, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationTable from '@/components/ConversationTable.vue'
import type { ConversationItem } from '@/api/feedback'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

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
    appversion: '1.0',
    channel: 'wetype',
    service_vid: 10000,
    external_chat_url: 'https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=u1',
    platform: 'iOS',
    preview_text: '闪退了',
  }
}

describe('ConversationTable', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('opens original chat from the table action', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mount(ConversationTable, {
      props: { items: [item()] },
      global: {
        directives: { loading: {} },
        stubs: {
          L1Tag: true,
          SeverityTag: true,
          ElIcon: true,
          ElTable: { template: '<div><slot /></div>', props: ['data'] },
          ElTableColumn: {
            template: '<div><slot name="default" :row="row" /></div>',
            props: ['label', 'width', 'minWidth'],
            setup() {
              return { row: item() }
            },
          },
          ElButton: {
            template: '<button :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
            props: ['disabled'],
          },
        },
      },
    })

    await wrapper.find('button').trigger('click')

    expect(wrapper.find('button').text()).toBe('链接')
    expect(open).toHaveBeenCalledWith(
      'https://wrfeedback.weread.woa.com/chat?channel=wetype&serviceVid=10000&userVid=u1',
      '_blank',
      'noopener,noreferrer',
    )
  })
})
