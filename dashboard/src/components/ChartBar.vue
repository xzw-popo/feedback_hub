<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref, shallowRef } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, GridComponent, TitleComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ data: Record<string, number>; topN?: number; height?: string }>()
const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

function render() {
  if (!chart.value) return
  const entries = Object.entries(props.data).sort((a, b) => b[1] - a[1])
  const top = props.topN ? entries.slice(0, props.topN) : entries
  const names = top.map(([k]) => k).reverse()
  const values = top.map(([, v]) => v).reverse()
  chart.value.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 16, top: 16, bottom: 24 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: names },
    series: [{ type: 'bar', data: values, itemStyle: { color: '#3b82f6' } }],
  })
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
watch(() => props.data, render, { deep: true })
</script>

<template>
  <div ref="el" :style="{ height: height ?? '280px', width: '100%' }" />
</template>
