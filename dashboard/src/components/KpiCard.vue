<script setup lang="ts">
defineProps<{ title: string; value: string | number; tooltip?: string }>()
</script>

<template>
  <div class="card kpi">
    <div class="kpi-title">
      {{ title }}
      <el-tooltip
        v-if="tooltip"
        :content="tooltip"
        placement="top"
      >
        <span
          class="kpi-hint"
          :title="tooltip"
        >ⓘ</span>
      </el-tooltip>
    </div>
    <div class="kpi-value font-mono">
      {{ value }}
    </div>
  </div>
</template>

<style scoped>
.kpi { padding: 16px 20px; height: 96px; display: flex; flex-direction: column; justify-content: center; }
.kpi-title { color: var(--text-muted); font-size: 13px; margin-bottom: 8px; }
.kpi-value { color: var(--text); font-size: 24px; font-weight: 700; }
.kpi-hint { color: var(--text-muted); cursor: help; margin-left: 4px; }
</style>
