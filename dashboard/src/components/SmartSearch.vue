<script setup lang="ts">
import { injectSearchState } from '@/composables/useSearchState'
import type { MetadataFilters } from '@/api/feedback'

const props = defineProps<{ filters: MetadataFilters }>()

const {
  state,
  hasSmartQuery,
  hasKeywordQuery,
  doSmartSearch,
} = injectSearchState()

function onSearch() {
  if (!hasSmartQuery.value) return
  void doSmartSearch(props.filters)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') onSearch()
}
</script>

<template>
  <div class="smart-search">
    <el-input
      v-model="state.smartQuery"
      placeholder="用自然语言描述你想查找的反馈..."
      size="large"
      clearable
      :disabled="state.keywordLoading"
      @keydown="onKeydown"
    >
      <template #append>
        <el-button
          type="primary"
          :loading="state.smartLoading"
          :disabled="!hasSmartQuery || state.keywordLoading"
          @click="onSearch"
        >
          AI 搜索
        </el-button>
      </template>
    </el-input>
    <div
      v-if="state.smartLoading"
      class="smart-search-hint"
    >
      <el-icon class="is-loading"><i class="el-icon-loading" /></el-icon>
      AI 正在理解你的意图...
    </div>
  </div>
</template>

<style scoped>
.smart-search {
  width: 100%;
}
.smart-search-hint {
  margin-top: 8px;
  color: var(--el-color-primary);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
</style>
