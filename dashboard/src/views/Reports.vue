<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import {
  getSearchReport,
  listSearchReports,
  retrySearchReport,
  type SearchReportJob,
} from '@/api/feedback'
import { renderMarkdown } from '@/utils/markdown'

const route = useRoute()

const reportsLoading = ref(false)
const reportDetailLoading = ref(false)
const reportJobs = ref<SearchReportJob[]>([])
const selectedReport = ref<SearchReportJob | null>(null)
let reportPollTimer: number | null = null

const hasRunningReports = computed(() =>
  reportJobs.value.some(job => job.status === 'pending' || job.status === 'running')
)
const renderedReportHtml = computed(() =>
  selectedReport.value?.result_markdown
    ? renderMarkdown(selectedReport.value.result_markdown)
    : ''
)

async function refreshReportJobs() {
  reportsLoading.value = true
  try {
    const resp = await listSearchReports(30)
    reportJobs.value = resp.items
    if (selectedReport.value) {
      const fresh = resp.items.find(job => job.id === selectedReport.value?.id)
      if (fresh) selectedReport.value = { ...selectedReport.value, ...fresh }
    }
    if (!selectedReport.value && resp.items.length > 0) {
      await openReportDetail(resp.items[0].id)
    }
    ensureReportPolling()
  } finally {
    reportsLoading.value = false
  }
}

async function openReportDetail(id: string) {
  reportDetailLoading.value = true
  try {
    selectedReport.value = await getSearchReport(id)
  } finally {
    reportDetailLoading.value = false
  }
}

async function retryReport(job: SearchReportJob) {
  await retrySearchReport(job.id)
  ElMessage.success('已重新提交报告任务')
  await refreshReportJobs()
  await openReportDetail(job.id)
  ensureReportPolling()
}

async function copyReportMarkdown() {
  if (!selectedReport.value?.result_markdown) return
  await navigator.clipboard.writeText(selectedReport.value.result_markdown)
  ElMessage.success('已复制 Markdown')
}

function ensureReportPolling() {
  if (hasRunningReports.value && reportPollTimer === null) {
    reportPollTimer = window.setInterval(() => {
      void refreshReportJobs()
      if (selectedReport.value?.status === 'pending' || selectedReport.value?.status === 'running') {
        void openReportDetail(selectedReport.value.id)
      }
    }, 5000)
  } else if (!hasRunningReports.value && reportPollTimer !== null) {
    window.clearInterval(reportPollTimer)
    reportPollTimer = null
  }
}

function reportStatusText(status: SearchReportJob['status']) {
  return {
    pending: '排队中',
    running: '生成中',
    succeeded: '已完成',
    failed: '生成失败',
  }[status]
}

function reportStatusType(status: SearchReportJob['status']) {
  return status === 'succeeded'
    ? 'success'
    : status === 'failed'
      ? 'danger'
      : 'warning'
}

watch(
  () => route.query.job,
  job => {
    if (typeof job === 'string' && job) {
      void openReportDetail(job)
    }
  },
)

onMounted(async () => {
  await refreshReportJobs()
  if (typeof route.query.job === 'string' && route.query.job) {
    await openReportDetail(route.query.job)
  }
})

onUnmounted(() => {
  if (reportPollTimer !== null) {
    window.clearInterval(reportPollTimer)
  }
})
</script>

<template>
  <div class="page reports-page">
    <div class="reports-header">
      <h1 class="page-title">
        报告
      </h1>
      <el-button
        :loading="reportsLoading"
        @click="refreshReportJobs"
      >
        刷新
      </el-button>
    </div>

    <div class="reports-layout">
      <aside class="reports-list card">
        <div class="reports-list-title">
          最近报告
        </div>
        <el-empty
          v-if="!reportsLoading && reportJobs.length === 0"
          description="暂无报告"
        />
        <button
          v-for="job in reportJobs"
          :key="job.id"
          class="report-job"
          :class="{ active: selectedReport?.id === job.id }"
          type="button"
          @click="openReportDetail(job.id)"
        >
          <span class="report-job-title">{{ job.title }}</span>
          <span class="report-job-meta">
            {{ dayjs(job.created_at).format('MM-DD HH:mm') }}
            · 样本 {{ job.sample_count }}
          </span>
          <el-tag
            size="small"
            :type="reportStatusType(job.status)"
          >
            {{ reportStatusText(job.status) }}
          </el-tag>
        </button>
      </aside>

      <section class="report-detail card">
        <el-empty
          v-if="!selectedReport"
          description="选择一份报告"
        />
        <template v-else>
          <div class="report-detail-header">
            <div>
              <h2>{{ selectedReport.title }}</h2>
              <p class="muted">
                {{ reportStatusText(selectedReport.status) }}
                <template v-if="selectedReport.finished_at">
                  · {{ dayjs(selectedReport.finished_at).format('YYYY-MM-DD HH:mm') }}
                </template>
              </p>
            </div>
            <div class="report-detail-actions">
              <el-button
                v-if="selectedReport.status === 'failed'"
                @click="retryReport(selectedReport)"
              >
                重试
              </el-button>
              <el-button
                :disabled="!selectedReport.result_markdown"
                @click="copyReportMarkdown"
              >
                复制 Markdown
              </el-button>
            </div>
          </div>

          <el-skeleton
            v-if="reportDetailLoading"
            :rows="8"
            animated
          />
          <el-result
            v-else-if="selectedReport.status === 'failed'"
            icon="error"
            title="生成失败"
            :sub-title="selectedReport.error_message || 'AI 服务暂时不可用'"
          />
          <el-result
            v-else-if="selectedReport.status === 'pending' || selectedReport.status === 'running'"
            icon="info"
            :title="reportStatusText(selectedReport.status)"
            sub-title="报告生成后会自动刷新"
          />
          <div
            v-else
            class="report-markdown"
            v-html="renderedReportHtml"
          />
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.reports-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.reports-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.reports-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 16px;
  min-height: 0;
  flex: 1;
}
.reports-list,
.report-detail {
  min-height: 0;
  overflow: auto;
}
.reports-list-title {
  margin-bottom: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.report-job {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 8px;
  text-align: left;
  cursor: pointer;
  display: grid;
  gap: 6px;
}
.report-job:hover,
.report-job.active {
  border-color: var(--el-color-primary);
}
.report-job-title {
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.report-job-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.report-detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.report-detail-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.report-detail-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.report-markdown {
  margin: 0;
  padding: 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-primary);
  overflow-wrap: anywhere;
  line-height: 1.7;
}
.report-markdown :deep(h1),
.report-markdown :deep(h2),
.report-markdown :deep(h3) {
  margin: 18px 0 8px;
  color: var(--el-text-color-primary);
  line-height: 1.35;
}
.report-markdown :deep(h1) {
  margin-top: 0;
  font-size: 22px;
}
.report-markdown :deep(h2) {
  font-size: 18px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 4px;
}
.report-markdown :deep(h3) {
  font-size: 15px;
}
.report-markdown :deep(p) {
  margin: 8px 0;
}
.report-markdown :deep(ul),
.report-markdown :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
}
.report-markdown :deep(li) {
  margin: 4px 0;
}
.report-markdown :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--el-fill-color);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.92em;
}
.report-markdown :deep(pre) {
  margin: 10px 0;
  padding: 10px;
  overflow: auto;
  border-radius: 6px;
  background: var(--el-fill-color);
}
.report-markdown :deep(pre code) {
  padding: 0;
  background: transparent;
}
.report-markdown :deep(table) {
  width: 100%;
  margin: 12px 0;
  border-collapse: collapse;
  display: block;
  overflow-x: auto;
}
.report-markdown :deep(th),
.report-markdown :deep(td) {
  border: 1px solid var(--el-border-color);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
.report-markdown :deep(th) {
  background: var(--el-fill-color);
  font-weight: 600;
}
@media (max-width: 900px) {
  .reports-layout {
    grid-template-columns: 1fr;
  }
  .report-detail-header {
    flex-direction: column;
  }
}
</style>
