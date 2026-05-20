<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import dayjs from 'dayjs'
import FilterBar from '@/components/FilterBar.vue'
import ConversationTable from '@/components/ConversationTable.vue'
import { useUrlQuery } from '@/composables/useUrlQuery'
import {
  listConversations,
  exportCsvUrl,
  type ConversationItem,
  type ListParams,
} from '@/api/feedback'

const defaultFrom = dayjs().subtract(7, 'day').format('YYYY-MM-DD')
const defaultTo = dayjs().format('YYYY-MM-DD')

const url = useUrlQuery({
  q: '',
  L1: '',
  L2: '',
  severity: '',
  from: defaultFrom,
  to: defaultTo,
  page: '1',
  pageSize: '50',
})

// 给 FilterBar 用的 v-model 对象（不含 page/pageSize）
const filterState = computed({
  get() {
    return {
      q: url.q,
      L1: url.L1,
      L2: url.L2,
      severity: url.severity,
      from: url.from,
      to: url.to,
    }
  },
  set(v) {
    url.q = v.q
    url.L1 = v.L1
    url.L2 = v.L2
    url.severity = v.severity
    url.from = v.from
    url.to = v.to
  },
})

const items = ref<ConversationItem[]>([])
const total = ref(0)
const loading = ref(false)

function buildParams(): ListParams {
  const page = Number(url.page) || 1
  const pageSize = Number(url.pageSize) || 50
  return {
    q: url.q || undefined,
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
  loading.value = true
  try {
    const r = await listConversations(buildParams())
    items.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

function onApply() {
  url.page = '1'
  void load()
}

function onClear() {
  url.q = ''
  url.L1 = ''
  url.L2 = ''
  url.severity = ''
  url.from = defaultFrom
  url.to = defaultTo
  url.page = '1'
  void load()
}

function onExport() {
  const u = exportCsvUrl({
    q: url.q || undefined,
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

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">
      反馈列表
    </h1>

    <FilterBar
      v-model="filterState"
      @apply="onApply"
      @clear="onClear"
      @export="onExport"
    />

    <div class="card table-card">
      <ConversationTable
        :items="items"
        :loading="loading"
      />

      <div class="pager">
        <span class="muted">共 {{ total }} 条</span>
        <el-pagination
          background
          layout="prev, pager, next, sizes"
          :total="total"
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
.table-card {
  margin-top: 16px;
}
.pager {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: flex-end;
}
</style>
