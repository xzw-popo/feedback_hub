import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import App from '@/App.vue'

vi.mock('@/composables/useApiHealth', () => ({
  useApiHealth: () => ({ healthy: true }),
}))

describe('App navigation', () => {
  it('shows primary navigation in product order without the test feedback list', () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          RouterView: true,
          RouterLink: {
            props: ['to'],
            template: '<a class="nav-item" :href="to"><slot /></a>',
          },
        },
      },
    })

    const labels = wrapper.findAll('.nav-item').map(link => link.text())

    expect(labels).toEqual(['反馈列表', '微博舆情', '报告', '概览测试'])
  })
})
