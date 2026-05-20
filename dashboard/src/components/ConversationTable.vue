<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { ConversationItem } from '@/api/feedback'
import L1Tag from './L1Tag.vue'
import SeverityTag from './SeverityTag.vue'
import { formatTs, truncate } from '@/utils/format'

defineProps<{ items: ConversationItem[]; loading?: boolean; emptyText?: string }>()

const router = useRouter()
function gotoDetail(row: ConversationItem) {
  router.push(`/feedback/${row.conversation_id}`)
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
      min-width="320"
    >
      <template #default="{ row }">
        {{ truncate(row.preview_text, 80) }}
      </template>
    </el-table-column>
    <el-table-column
      label="L1"
      width="100"
    >
      <template #default="{ row }">
        <L1Tag :value="row.L1" />
      </template>
    </el-table-column>
    <el-table-column
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
      label="severity"
      width="100"
    >
      <template #default="{ row }">
        <SeverityTag :value="row.severity" />
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
  </el-table>
</template>

<style scoped>
:deep(.clickable-row) { cursor: pointer; }
:deep(.clickable-row:hover) { background: #f7f8fa; }
</style>
