<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import dayjs from 'dayjs'
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

const PLATFORM_OPTIONS = ['iOS', 'Android', 'Win', 'Mac', '小程序', '未知']

const defaultFrom = dayjs().subtract(7, 'day').format('YYYY-MM-DD')
const defaultTo = dayjs().format('YYYY-MM-DD')

const url = useUrlQuery({
  platform: '',
  from: defaultFrom,
  to: defaultTo,
  page: '1',
  pageSize: '50',
})

// 搜索状态
const searchState = provideSearchState()

// 设备多选
const selectedPlatforms = ref<string[]>([])

function onPlatformChange(vals: string[]) {
  selectedPlatforms.value = vals
  url.platform = vals.join(',')
}

// 日期范围
const dateRange = computed<[string, string] | null>({
  get() {
    if (url.from && url.to) {
      return [url.from, url.to]
    }
    return null
  },
  set(v) {
    url.from = v?.[0] ?? ''
    url.to = v?.[1] ?? ''
  },
})

// 元数据筛选参数（传给搜索组件）
const metadataFilters = computed(() => ({
  from: url.from || undefined,
  to: url.to || undefined,
  platform: url.platform || undefined,
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
    platform: url.platform || undefined,
    from: url.from || undefined,
    to: url.to || undefined,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  }
}

async function load() {
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
  selectedPlatforms.value = []
  url.platform = ''
  url.from = defaultFrom
  url.to = defaultTo
  url.page = '1'
  searchState.clearAllSearch()
  void load()
}

function onExport() {
  const u = exportCsvUrl({
    platform: url.platform || undefined,
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
  // 从 URL 恢复 platform 多选状态
  if (url.platform) {
    selectedPlatforms.value = url.platform.split(',').filter(Boolean)
  }
  void load()
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">
      反馈列表
    </h1>

    <div class="filter-bar card">
      <el-select
        :model-value="selectedPlatforms"
        placeholder="设备"
        clearable
        multiple
        collapse-tags
        collapse-tags-tooltip
        style="width: 220px"
        @update:model-value="onPlatformChange"
      >
        <el-option
          v-for="x in PLATFORM_OPTIONS"
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
        style="width: 200px"
      />
      <div class="filter-actions">
        <el-button
          type="primary"
          @click="onApply"
        >
          筛选
        </el-button>
        <el-button @click="onClear">
          清空
        </el-button>
        <el-button @click="onExport">
          导出 CSV
        </el-button>
      </div>
    </div>

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
        mode="device"
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
.filter-bar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
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
</style>
