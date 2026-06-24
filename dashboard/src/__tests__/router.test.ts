import { describe, expect, it } from 'vitest'
import { router } from '@/router'

describe('router', () => {
  it('exposes reports as a top-level route', () => {
    const route = router.getRoutes().find(r => r.path === '/reports')

    expect(route?.name).toBe('reports')
  })

  it('exposes weibo public opinion routes', () => {
    const workspace = router.getRoutes().find(r => r.path === '/weibo')
    const stats = router.getRoutes().find(r => r.path === '/weibo/stats')
    const legacyList = router.getRoutes().find(r => r.path === '/weibo/list')

    expect(workspace?.name).toBe('weibo')
    expect(stats?.name).toBe('weibo-stats')
    expect(legacyList?.redirect).toBe('/weibo')
  })
})
