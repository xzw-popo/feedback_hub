import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import KpiCard from '@/components/KpiCard.vue'

const globalStubs = { 'el-tooltip': { template: '<span><slot /></span>' } }

describe('KpiCard', () => {
  it('renders title and value', () => {
    const w = mount(KpiCard, {
      props: { title: '总会话', value: '142' },
      global: { stubs: globalStubs },
    })
    expect(w.text()).toContain('总会话')
    expect(w.text()).toContain('142')
  })
  it('shows tooltip text when provided', () => {
    const w = mount(KpiCard, {
      props: { title: '自动定级率', value: '78%', tooltip: '非待定占比' },
      global: { stubs: globalStubs },
    })
    expect(w.html()).toContain('非待定占比')
  })
})
