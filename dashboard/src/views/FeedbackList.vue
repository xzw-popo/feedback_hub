<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import ConversationTable from '@/components/ConversationTable.vue'
import SmartSearch from '@/components/SmartSearch.vue'
import KeywordBuilder from '@/components/KeywordBuilder.vue'
import FineFilterButton from '@/components/FineFilterButton.vue'
import { useUrlQuery } from '@/composables/useUrlQuery'
import { provideSearchState } from '@/composables/useSearchState'
import {
  listConversations,
  exportCsvUrl,
  createSearchReport,
  type ConversationItem,
  type ListParams,
  type CreateSearchReportRequest,
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
const router = useRouter()

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
const canGenerateReport = computed(() =>
  searchState.hasAnySearch.value && displayItems.value.length > 0 && !displayLoading.value
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

// 搜索报告状态
const reportDialogOpen = ref(false)
const reportTitle = ref('')
const reportSubmitting = ref(false)

const reportQuerySummary = computed(() => searchState.currentQueryIntent.value || '当前搜索结果')
const reportSnapshotCount = computed(() => displayItems.value.length)
const reportSampleLimit = computed(() => Math.min(displayItems.value.length, 80))

function defaultReportTitle() {
  const base = reportQuerySummary.value.trim() || '搜索反馈'
  return `${base.slice(0, 32)}分析报告`
}

function buildReportRequest(): CreateSearchReportRequest {
  const aiScores: CreateSearchReportRequest['ai_scores'] = {}
  for (const [id, score] of Object.entries(searchState.state.aiScores)) {
    aiScores[id] = { score }
  }
  return {
    title: reportTitle.value.trim() || defaultReportTitle(),
    query: reportQuerySummary.value,
    search_type: searchState.state.mode === 'smart' ? 'smart' : 'keyword',
    filters: metadataFilters.value,
    search_payload: {
      mode: searchState.state.mode,
      keyword_groups: searchState.state.keywordGroups,
      keyword_excludes: searchState.state.keywordExcludes,
      total: searchState.state.total,
    },
    conversation_ids: displayItems.value.map(item => item.conversation_id),
    ai_scores: aiScores,
  }
}

function openReportDialog() {
  if (!canGenerateReport.value) return
  reportTitle.value = defaultReportTitle()
  reportDialogOpen.value = true
}

async function submitReportJob() {
  reportSubmitting.value = true
  try {
    const created = await createSearchReport(buildReportRequest())
    reportDialogOpen.value = false
    ElMessage.success('报告任务已创建，可在「报告」中查看')
    await router.push({ path: '/reports', query: { job: created.id } })
  } finally {
    reportSubmitting.value = false
  }
}

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
        <div class="table-actions">
          <el-button
            :disabled="!canGenerateReport"
            :loading="reportSubmitting"
            @click="openReportDialog"
          >
            生成报告
          </el-button>
          <FineFilterButton />
        </div>
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

    <el-dialog
      v-model="reportDialogOpen"
      title="生成搜索报告"
      width="520px"
    >
      <el-form label-width="96px">
        <el-form-item label="报告标题">
          <el-input v-model="reportTitle" />
        </el-form-item>
        <el-form-item label="搜索问题">
          <span class="dialog-text">{{ reportQuerySummary }}</span>
        </el-form-item>
        <el-form-item label="命中结果">
          <span class="dialog-text">{{ reportSnapshotCount }} 条</span>
        </el-form-item>
        <el-form-item label="分析样本">
          <span class="dialog-text">最多 {{ reportSampleLimit }} 条</span>
        </el-form-item>
      </el-form>
      <p class="report-note">
        报告会基于当前结果快照异步生成，可能需要几分钟。
      </p>
      <template #footer>
        <el-button @click="reportDialogOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="reportSubmitting"
          @click="submitReportJob"
        >
          提交
        </el-button>
      </template>
    </el-dialog>
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
.table-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.dialog-text {
  color: var(--el-text-color-primary);
  overflow-wrap: anywhere;
}
.report-note {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.pager {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: flex-end;
}
</style>
