import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import WeiboList from '@/views/WeiboList.vue'

vi.mock('@/api/weibo', () => ({
  listWeiboPosts: vi.fn().mockResolvedValue({ total: 0, items: [] }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

describe('WeiboList', () => {
  it('labels the three select filters with clear placeholders', () => {
    const wrapper = mount(WeiboList, {
      global: {
        directives: { loading: {} },
        stubs: {
          ElButton: { template: '<button><slot /></button>' },
          ElDatePicker: { template: '<input :placeholder="placeholder" />', props: ['placeholder'] },
          ElInput: { template: '<input :placeholder="placeholder" />', props: ['placeholder'] },
          ElOption: true,
          ElPagination: true,
          ElSelect: { template: '<div data-test="select-filter" :data-placeholder="placeholder"><slot /></div>', props: ['placeholder'] },
          ElTag: { template: '<span><slot /></span>' },
          ElTable: { template: '<table><slot /></table>' },
          ElTableColumn: true,
        },
      },
    })

    const placeholders = wrapper.findAll('[data-test="select-filter"]').map(node => node.attributes('data-placeholder'))

    expect(placeholders).toEqual(['品牌', '情绪', '风险'])
  })
})
