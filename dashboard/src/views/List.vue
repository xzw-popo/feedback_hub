<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import dayjs from 'dayjs'
import FilterBar from '@/components/FilterBar.vue'
import ConversationTable from '@/components/ConversationTable.vue'
import SmartSearch from '@/components/SmartSearch.vue'
import KeywordBuilder from '@/components/KeywordBuilder.vue'
import FineFilterButton from '@/components/FineFilterButton.vue'
import { useUrlQuery } from '@/composables/useUrlQuery'
import { provideSearchState } from '@/composables/useSearchState'
import {
  listConversations,
  exportCsvUrl,
  type ConversationItem,
  type ListParams,
} from '@/api/feedback'

const defaultFrom = dayjs().subtract(7, 'day').format('YYYY-MM-DD')
const defaultTo = dayjs().format('YYYY-MM-DD')

const url = useUrlQuery({
  L1: '',
  L2: '',
  severity: '',
  from: defaultFrom,
  to: defaultTo,
  page: '1',
  pageSize: '50',
})

// 搜索状态
const searchState = provideSearchState()

// 给 FilterBar 用的 v-model 对象（不含 page/pageSize）
const filterState = computed({
  get() {
    return {
      L1: url.L1,
      L2: url.L2,
      severity: url.severity,
      from: url.from,
      to: url.to,
    }
  },
  set(v) {
    url.L1 = v.L1
    url.L2 = v.L2
    url.severity = v.severity
    url.from = v.from
    url.to = v.to
  },
})

// 元数据筛选参数（传给搜索组件）
const metadataFilters = computed(() => ({
  from: url.from || undefined,
  to: url.to || undefined,
  L1: url.L1 || undefined,
  L2: url.L2 || undefined,
  severity: url.severity || undefined,
}))

// 常规列表浏览数据
const normalItems = ref<ConversationItem[]>([])
const normalTotal = ref(0)
const normalLoading = ref(false)

// 展示数据：如果有搜索结果则用搜索结果，否则用常规浏览
const displayItems = computed(() =>
  searchState.hasAnySearch.value ? searchState.state.items : normalItems.value
)
const displayTotal = computed(() =>
  searchState.hasAnySearch.value ? searchState.state.total : normalTotal.value
)
const displayLoading = computed(() =>
  searchState.hasAnySearch.value
    ? searchState.state.smartLoading || searchState.state.keywordLoading
    : normalLoading.value
)

function buildParams(): ListParams {
  const page = Number(url.page) || 1
  const pageSize = Number(url.pageSize) || 50
  return {
    L1: url.L1 || undefined,
    L2: url.L2 || undefined,
    severity: url.severity || undefined,
    from: url.from || undefined,
    to: url.to || undefined,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  }
}

async function load() {
  // 如果有搜索条件，则由搜索组件管理数据，不走常规加载
  if (searchState.hasAnySearch.value) return
  normalLoading.value = true
  try {
    const r = await listConversations(buildParams())
    normalItems.value = r.items
    normalTotal.value = r.total
  } finally {
    normalLoading.value = false
  }
}

function onApply() {
  url.page = '1'
  void load()
}

function onClear() {
  url.L1 = ''
  url.L2 = ''
  url.severity = ''
  url.from = defaultFrom
  url.to = defaultTo
  url.page = '1'
  searchState.clearAllSearch()
  void load()
}

function onExport() {
  const u = exportCsvUrl({
    L1: url.L1 || undefined,
    L2: url.L2 || undefined,
    severity: url.severity || undefined,
    from: url.from || undefined,
    to: url.to || undefined,
  })
  window.location.href = u
}

function onPageChange(p: number) {
  url.page = String(p)
  void load()
}

function onSizeChange(s: number) {
  url.pageSize = String(s)
  url.page = '1'
  void load()
}

// 关键词构建器的 newKeywords 状态
const newKeywords = ref<Record<number, string>>({})

// 手动选取关键词折叠状态
const manualKeywordsOpen = ref<string[]>([])

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">
      反馈列表
      <span class="beta-inline">测试</span>
    </h1>

    <FilterBar
      v-model="filterState"
      @apply="onApply"
      @clear="onClear"
      @export="onExport"
    />

    <!-- 智能搜索区域 -->
    <div class="search-section card">
      <SmartSearch :filters="metadataFilters" />

      <el-collapse v-model="manualKeywordsOpen" class="manual-keywords-collapse">
        <el-collapse-item name="open">
          <template #title>
            <span class="collapse-title">手动选取关键词</span>
          </template>
          <KeywordBuilder
            v-model:new-keywords="newKeywords"
            :filters="metadataFilters"
          />
        </el-collapse-item>
      </el-collapse>
    </div>

    <div class="card table-card">
      <div class="table-header">
        <span class="muted">共 {{ displayTotal }} 条</span>
        <FineFilterButton />
      </div>
      <ConversationTable
        :items="displayItems"
        :loading="displayLoading"
        :ai-scores="searchState.state.aiScores"
        :fine-filtering="searchState.state.fineFiltering"
      />

      <div
        v-if="!searchState.hasAnySearch.value"
        class="pager"
      >
        <span class="muted">共 {{ normalTotal }} 条</span>
        <el-pagination
          background
          layout="prev, pager, next, sizes"
          :total="normalTotal"
          :page-sizes="[20, 50, 100]"
          :page-size="Number(url.pageSize) || 50"
          :current-page="Number(url.page) || 1"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-section {
  margin-top: 16px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.manual-keywords-collapse {
  border: none;
  margin-top: 4px;
}
.manual-keywords-collapse :deep(.el-collapse-item__header) {
  border-bottom: none;
  background: transparent;
  height: 32px;
  line-height: 32px;
  padding: 0;
}
/* 箭头移到最左端 */
.manual-keywords-collapse :deep(.el-collapse-item__arrow) {
  order: -1;
  margin-left: 0;
  margin-right: 6px;
}
.manual-keywords-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
  background: transparent;
}
.manual-keywords-collapse :deep(.el-collapse-item__content) {
  padding: 0;
}
.collapse-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.table-card {
  margin-top: 16px;
}
.table-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.pager {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: flex-end;
}
.beta-inline {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  font-size: 10px;
  font-weight: 500;
  line-height: 16px;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: 3px;
  vertical-align: middle;
}
</style>
