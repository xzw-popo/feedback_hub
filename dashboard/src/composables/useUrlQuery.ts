import { reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export function useUrlQuery<T extends Record<string, string>>(defaults: T): T {
  const route = useRoute()
  const router = useRouter()

  const initial = {} as T
  for (const k of Object.keys(defaults) as (keyof T)[]) {
    const fromUrl = route.query[k as string]
    if (typeof fromUrl === 'string' && fromUrl !== '') {
      ;(initial as Record<string, string>)[k as string] = fromUrl
    } else {
      ;(initial as Record<string, string>)[k as string] = defaults[k]
    }
  }

  const state = reactive(initial) as T

  watch(
    state,
    async (next) => {
      const query: Record<string, string> = {}
      for (const [k, v] of Object.entries(next)) {
        if (v !== '' && v != null) query[k] = v
      }
      await router.replace({ query })
    },
    { deep: true },
  )

  return state
}
