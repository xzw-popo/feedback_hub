import { describe, expect, it } from 'vitest'
import { router } from '@/router'

describe('router', () => {
  it('exposes reports as a top-level route', () => {
    const route = router.getRoutes().find(r => r.path === '/reports')

    expect(route?.name).toBe('reports')
  })
})
