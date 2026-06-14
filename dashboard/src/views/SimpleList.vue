<script setup lang="ts">
import { ref, onMounted } from 'vue'
import dayjs from 'dayjs'
import ConversationTable from '@/components/ConversationTable.vue'
import { listConversations, type ConversationItem, type ListParams } from '@/api/feedback'

const defaultFrom = dayjs().subtract(7, 'day').format('YYYY-MM-DD')
const defaultTo = dayjs().format('YYYY-MM-DD')

const dateRange = ref<[string, string]>([defaultFrom, defaultTo])
const page = ref(1)
const pageSize = ref(50)

const items = ref<ConversationItem[]>([])
const total = ref(0)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const params: ListParams = {
      from: dateRange.value[0] || undefined,
      to: dateRange.value[1] || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    }
    const r = await listConversations(params)
    items.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

function onApply() {
  page.value = 1
  void load()
}

function onClear() {
  dateRange.value = [defaultFrom, defaultTo]
  page.value = 1
  void load()
}

function onPageChange(p: number) {
  page.value = p
  void load()
}

function onSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
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

    <!-- 仅日期筛选 -->
    <div class="filter-bar card">
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        value-format="YYYY-MM-DD"
        start-placeholder="起始日期"
        end-placeholder="结束日期"
        style="width: 280px"
      />
      <el-button
        type="primary"
        @click="onApply"
      >
        筛选
      </el-button>
      <el-button @click="onClear">
        清空
      </el-button>
    </div>

    <!-- 表格 -->
    <div class="card table-card">
      <div class="table-header">
        <span class="muted">共 {{ total }} 条</span>
      </div>
      <ConversationTable
        :items="items"
        :loading="loading"
        mode="simple"
      />

      <div class="pager">
        <span class="muted">共 {{ total }} 条</span>
        <el-pagination
          background
          layout="prev, pager, next, sizes"
          :total="total"
          :page-sizes="[20, 50, 100]"
          :page-size="pageSize"
          :current-page="page"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.table-card { margin-top: 16px; }
.table-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.pager { margin-top: 16px; display: flex; align-items: center; gap: 16px; justify-content: flex-end; }
</style>
