<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { ConversationItem } from '@/api/feedback'
import type { AiScoreMap } from '@/composables/useSearchState'
import L1Tag from './L1Tag.vue'
import SeverityTag from './SeverityTag.vue'
import { formatTs, truncate } from '@/utils/format'

const props = defineProps<{
  items: ConversationItem[]
  loading?: boolean
  emptyText?: string
  aiScores?: AiScoreMap
  fineFiltering?: boolean
  /** 'full' 显示所有列（默认），'simple' 只显示时间/反馈/平台，'device' 显示时间/反馈/设备 */
  mode?: 'full' | 'simple' | 'device'
}>()

const router = useRouter()
function gotoDetail(row: ConversationItem) {
  router.push(`/feedback/${row.conversation_id}`)
}

function scoreLabel(score: 1 | 2 | 3): string {
  return '⭐'.repeat(score)
}

function scoreClass(score: 1 | 2 | 3): string {
  if (score === 3) return 'score-high'
  if (score === 2) return 'score-mid'
  return 'score-low'
}
</script>

<template>
  <el-table
    v-loading="loading"
    :data="items"
    style="width: 100%"
    :empty-text="emptyText ?? '暂无数据'"
    row-class-name="clickable-row"
    @row-click="gotoDetail"
  >
    <el-table-column
      label="时间"
      width="160"
    >
      <template #default="{ row }">
        <span class="font-mono">{{ formatTs(row.last_ts_ms) }}</span>
      </template>
    </el-table-column>
    <el-table-column
      label="反馈片段"
      :min-width="mode === 'simple' || mode === 'device' ? 480 : 320"
    >
      <template #default="{ row }">
        {{ truncate(row.preview_text, 80) }}
      </template>
    </el-table-column>
    <el-table-column
      v-if="mode !== 'simple' && mode !== 'device'"
      label="L1"
      width="100"
    >
      <template #default="{ row }">
        <L1Tag :value="row.L1" />
      </template>
    </el-table-column>
    <el-table-column
      v-if="mode !== 'simple' && mode !== 'device'"
      label="L2"
      width="200"
    >
      <template #default="{ row }">
        <span
          v-if="row.L2.length === 0"
          class="muted"
        >—</span>
        <span v-else>{{ row.L2.join(' / ') }}</span>
      </template>
    </el-table-column>
    <el-table-column
      v-if="mode !== 'simple' && mode !== 'device'"
      label="severity"
      width="100"
    >
      <template #default="{ row }">
        <SeverityTag :value="row.severity" />
      </template>
    </el-table-column>
    <el-table-column
      v-if="mode === 'device'"
      label="设备"
      width="100"
    >
      <template #default="{ row }">
        <span>{{ row.platform || '—' }}</span>
      </template>
    </el-table-column>
    <el-table-column
      label="平台·版本"
      width="160"
    >
      <template #default="{ row }">
        <span class="muted">{{ row.appversion ?? '—' }}</span>
      </template>
    </el-table-column>
    <el-table-column
      v-if="aiScores && Object.keys(aiScores).length > 0"
      label="AI 相关性"
      width="120"
      sortable
      :sort-method="(a: ConversationItem, b: ConversationItem) => (aiScores![b.conversation_id] ?? 0) - (aiScores![a.conversation_id] ?? 0)"
    >
      <template #default="{ row }">
        <span
          v-if="aiScores![row.conversation_id]"
          :class="scoreClass(aiScores![row.conversation_id])"
        >{{ scoreLabel(aiScores![row.conversation_id]) }}</span>
        <el-icon
          v-else-if="fineFiltering"
          class="is-loading"
        ><i class="el-icon-loading" /></el-icon>
        <span
          v-else
          class="muted"
        >—</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
:deep(.clickable-row) { cursor: pointer; }
:deep(.clickable-row:hover) { background: #f7f8fa; }
.score-high { color: #67c23a; font-size: 14px; }
.score-mid { color: #e6a23c; font-size: 14px; }
.score-low { color: #c0c4cc; font-size: 14px; }
</style>
