import { ref, onMounted, onBeforeUnmount } from 'vue'
import { getDistribution } from '@/api/feedback'

export function useApiHealth(intervalMs = 30000) {
  const healthy = ref(true)
  let timer: ReturnType<typeof setInterval> | null = null

  async function check() {
    try {
      await getDistribution({})
      healthy.value = true
    } catch {
      healthy.value = false
    }
  }

  onMounted(() => {
    void check()
    timer = setInterval(check, intervalMs)
  })

  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
  })

  return { healthy }
}
