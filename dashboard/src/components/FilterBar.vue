<script setup lang="ts">
import { computed } from 'vue'
import { L1_VALUES, L2_VALUES, SEVERITY_VALUES } from '@/constants/labels'

interface FilterState {
  L1: string
  L2: string
  severity: string
  from: string
  to: string
}

const props = defineProps<{ modelValue: FilterState }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: FilterState): void
  (e: 'apply'): void
  (e: 'clear'): void
  (e: 'export'): void
}>()

const dateRange = computed<[string, string] | null>({
  get() {
    if (props.modelValue.from && props.modelValue.to) {
      return [props.modelValue.from, props.modelValue.to]
    }
    return null
  },
  set(v) {
    emit('update:modelValue', {
      ...props.modelValue, from: v?.[0] ?? '', to: v?.[1] ?? '',
    })
  },
})

function update<K extends keyof FilterState>(k: K, v: FilterState[K]) {
  emit('update:modelValue', { ...props.modelValue, [k]: v })
}
</script>

<template>
  <div class="filter-bar card">
    <el-select
      :model-value="modelValue.L1"
      placeholder="L1"
      clearable
      style="width: 120px"
      @update:model-value="update('L1', $event ?? '')"
    >
      <el-option
        v-for="x in L1_VALUES"
        :key="x"
        :label="x"
        :value="x"
      />
    </el-select>
    <el-select
      :model-value="modelValue.L2"
      placeholder="L2"
      clearable
      filterable
      style="width: 160px"
      @update:model-value="update('L2', $event ?? '')"
    >
      <el-option
        v-for="x in L2_VALUES"
        :key="x"
        :label="x"
        :value="x"
      />
    </el-select>
    <el-select
      :model-value="modelValue.severity"
      placeholder="severity"
      clearable
      style="width: 120px"
      @update:model-value="update('severity', $event ?? '')"
    >
      <el-option
        v-for="x in SEVERITY_VALUES"
        :key="x"
        :label="x"
        :value="x"
      />
    </el-select>
    <el-date-picker
      v-model="dateRange"
      type="daterange"
      value-format="YYYY-MM-DD"
      start-placeholder="起"
      end-placeholder="止"
      style="width: 240px"
    />
    <div class="filter-actions">
      <el-button
        type="primary"
        @click="emit('apply')"
      >
        筛选
      </el-button>
      <el-button @click="emit('clear')">
        清空
      </el-button>
      <el-button @click="emit('export')">
        导出 CSV
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
</style>
