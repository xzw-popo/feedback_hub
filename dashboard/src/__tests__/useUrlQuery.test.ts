import { describe, it, expect } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'
import { defineComponent, h, nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { useUrlQuery } from '@/composables/useUrlQuery'

function makeRouter(initialPath = '/list') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/list', component: { template: '<div />' } }],
  })
  router.push(initialPath)
  return router
}

const Probe = defineComponent({
  props: { defaults: { type: Object, required: true } },
  setup(props) {
    const state = useUrlQuery(props.defaults as Record<string, string>)
    return { state }
  },
  render() { return h('pre', JSON.stringify(this.state)) },
})

describe('useUrlQuery', () => {
  it('reads initial values from URL', async () => {
    const router = makeRouter('/list?L1=A.Bug&from=2026-05-15')
    await router.isReady()
    const w = mount(Probe, {
      props: { defaults: { L1: '', from: '' } },
      global: { plugins: [router] },
    })
    expect(w.vm.state.L1).toBe('A.Bug')
    expect(w.vm.state.from).toBe('2026-05-15')
  })

  it('applies defaults when URL has no params', async () => {
    const router = makeRouter('/list')
    await router.isReady()
    const w = mount(Probe, {
      props: { defaults: { L1: '', from: '2026-05-14' } },
      global: { plugins: [router] },
    })
    expect(w.vm.state.from).toBe('2026-05-14')
  })

  it('writing state updates URL query', async () => {
    const router = makeRouter('/list')
    await router.isReady()
    const w = mount(Probe, {
      props: { defaults: { L1: '' } },
      global: { plugins: [router] },
    })
    w.vm.state.L1 = 'B.建议'
    await nextTick()
    await flushPromises()
    expect(router.currentRoute.value.query.L1).toBe('B.建议')
  })
})
