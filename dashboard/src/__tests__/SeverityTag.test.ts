import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SeverityTag from '@/components/SeverityTag.vue'

describe('SeverityTag', () => {
  it('renders severity text', () => {
    const w = mount(SeverityTag, { props: { value: 'P0' } })
    expect(w.text()).toBe('P0')
  })
  it('uses red color for P0', () => {
    const w = mount(SeverityTag, { props: { value: 'P0' } })
    expect(w.attributes('style')).toContain('#ef4444')
  })
  it('renders dash when value is null', () => {
    const w = mount(SeverityTag, { props: { value: null } })
    expect(w.text()).toBe('—')
  })
})
