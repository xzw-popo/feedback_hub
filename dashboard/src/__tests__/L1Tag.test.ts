import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import L1Tag from '@/components/L1Tag.vue'

describe('L1Tag', () => {
  it('renders L1 text', () => {
    const w = mount(L1Tag, { props: { value: 'A.Bug' } })
    expect(w.text()).toBe('A.Bug')
  })
  it('uses red color for A.Bug', () => {
    const w = mount(L1Tag, { props: { value: 'A.Bug' } })
    expect(w.attributes('style')).toContain('#ef4444')
  })
  it('falls back to neutral for unknown L1', () => {
    const w = mount(L1Tag, { props: { value: 'Z.未知' } })
    expect(w.text()).toBe('Z.未知')
  })
})
