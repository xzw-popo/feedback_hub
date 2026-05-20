<script setup lang="ts">
import { computed } from 'vue'
import { L1_COLOR } from '@/constants/labels'

const props = defineProps<{ value: string | null | undefined }>()
const NEUTRAL = { fg: '#6b7280', bg: '#f3f4f6' }

const style = computed(() => {
  if (!props.value) return {}
  const c = (L1_COLOR as Record<string, { fg: string; bg: string }>)[props.value] ?? NEUTRAL
  return {
    color: c.fg, background: c.bg,
    padding: '2px 8px', borderRadius: '4px',
    fontSize: '12px', fontWeight: 600, display: 'inline-block',
  }
})
</script>

<template>
  <span v-if="value" :style="style">{{ value }}</span>
  <span v-else class="muted">—</span>
</template>
