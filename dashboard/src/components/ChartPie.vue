<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref, shallowRef } from 'vue'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([PieChart, TitleComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  data: Record<string, number>
  colorMap?: Record<string, string>
  height?: string
}>()

const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

function render() {
  if (!chart.value) return
  const items = Object.entries(props.data).map(([name, value]) => ({
    name, value,
    itemStyle: props.colorMap?.[name] ? { color: props.colorMap[name] } : undefined,
  }))
  chart.value.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{
      type: 'pie',
      radius: ['40%', '65%'],
      center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: '{b}\n{d}%' },
      data: items,
    }],
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
