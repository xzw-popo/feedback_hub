<script setup lang="ts">
import { computed } from 'vue'
import { SEVERITY_COLOR, type Severity } from '@/constants/labels'

const props = defineProps<{ value: Severity | null | undefined }>()

const style = computed(() => {
  if (!props.value) return {}
  const c = SEVERITY_COLOR[props.value]
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
