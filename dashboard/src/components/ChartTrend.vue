<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref, shallowRef, computed } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { TrendBucket } from '@/api/feedback'
import { L1_COLOR, L1_VALUES } from '@/constants/labels'

echarts.use([LineChart, GridComponent, TitleComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ buckets: TrendBucket[]; height?: string }>()
const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

const series = computed(() => {
  const l1Set = new Set<string>()
  for (const b of props.buckets) for (const k of Object.keys(b.counts)) l1Set.add(k)
  const l1List = (L1_VALUES as readonly string[]).filter((v) => l1Set.has(v)).concat(
    [...l1Set].filter((v) => !(L1_VALUES as readonly string[]).includes(v)),
  )
  return l1List.map((l1) => ({
    name: l1,
    type: 'line',
    stack: 'total',
    areaStyle: {},
    smooth: true,
    itemStyle: { color: (L1_COLOR as Record<string, { fg: string; bg: string }>)[l1]?.fg ?? '#6b7280' },
    data: props.buckets.map((b) => b.counts[l1] ?? 0),
  }))
})

function render() {
  if (!chart.value) return
  chart.value.setOption({
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, type: 'scroll' },
    grid: { left: 40, right: 24, top: 16, bottom: 48 },
    xAxis: { type: 'category', data: props.buckets.map((b) => b.bucket) },
    yAxis: { type: 'value' },
    series: series.value,
  }, true)
}

function resize() { chart.value?.resize() }

onMounted(() => {
  if (el.value) {
    chart.value = echarts.init(el.value)
    render()
    window.addEventListener('resize', resize)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
})
watch(() => props.buckets, render, { deep: true })
</script>

<template>
  <div
    ref="el"
    :style="{ height: height ?? '320px', width: '100%' }"
  />
</template>
