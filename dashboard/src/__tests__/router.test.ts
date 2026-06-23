import { describe, expect, it } from 'vitest'
import { router } from '@/router'

describe('router', () => {
  it('exposes reports as a top-level route', () => {
    const route = router.getRoutes().find(r => r.path === '/reports')

    expect(route?.name).toBe('reports')
  })

  it('exposes weibo public opinion routes', () => {
    const overview = router.getRoutes().find(r => r.path === '/weibo')
    const list = router.getRoutes().find(r => r.path === '/weibo/list')

    expect(overview?.name).toBe('weibo-overview')
    expect(list?.name).toBe('weibo-list')
  })
})
