<script setup lang="ts">
import { onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { listWeiboPosts, type WeiboPostItem } from '@/api/weibo'
import { useUrlQuery } from '@/composables/useUrlQuery'
import {
  BRAND_FOCUS_LABEL,
  BRAND_FOCUS_OPTIONS,
  RISK_LEVEL_LABEL,
  RISK_LEVEL_OPTIONS,
  SENTIMENT_LABEL,
  SENTIMENT_OPTIONS,
  TOPIC_LABEL,
  labelFor,
} from '@/constants/weibo'
import { formatTs, truncate } from '@/utils/format'

const defaultFrom = dayjs().subtract(7, 'day').format('YYYY-MM-DD')
const defaultTo = dayjs().format('YYYY-MM-DD')
const url = useUrlQuery({
  q: '',
  brand_focus: '',
  sentiment: '',
  risk_level: '',
  topic: '',
  from: defaultFrom,
  to: defaultTo,
  page: '1',
  pageSize: '50',
})

const items = ref<WeiboPostItem[]>([])
const total = ref(0)
const loading = ref(false)

function buildParams() {
  const page = Number(url.page) || 1
  const pageSize = Number(url.pageSize) || 50
  return {
    q: url.q || undefined,
    brand_focus: url.brand_focus || undefined,
    sentiment: url.sentiment || undefined,
    risk_level: url.risk_level || undefined,
    topic: url.topic || undefined,
    from: url.from || undefined,
    to: url.to || undefined,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  }
}

async function load() {
  loading.value = true
  try {
    const resp = await listWeiboPosts(buildParams())
    items.value = resp.items
    total.value = resp.total
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
  url.brand_focus = ''
  url.sentiment = ''
  url.risk_level = ''
  url.topic = ''
  url.from = defaultFrom
  url.to = defaultTo
  url.page = '1'
  void load()
}

function onPageChange(page: number) {
  url.page = String(page)
  void load()
}

function onSizeChange(size: number) {
  url.pageSize = String(size)
  url.page = '1'
  void load()
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page">
    <div class="weibo-list-header">
      <div>
        <h1 class="page-title">微博舆情</h1>
        <div class="muted">最近公开微博反馈，先扫舆情，再筛选和处理</div>
      </div>
      <el-button type="primary" plain @click="$router.push('/weibo/stats')">查看统计</el-button>
    </div>

    <div class="card filter-card">
      <el-input
        v-model="url.q"
        class="filter-input"
        placeholder="搜索正文关键词"
        clearable
        @keyup.enter="onApply"
      />
      <el-select v-model="url.brand_focus" class="filter-select">
        <el-option
          v-for="option in BRAND_FOCUS_OPTIONS"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-select v-model="url.sentiment" class="filter-select">
        <el-option
          v-for="option in SENTIMENT_OPTIONS"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-select v-model="url.risk_level" class="filter-select">
        <el-option
          v-for="option in RISK_LEVEL_OPTIONS"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-date-picker
        v-model="url.from"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="开始日期"
      />
      <el-date-picker
        v-model="url.to"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="结束日期"
      />
      <el-button type="primary" @click="onApply">筛选</el-button>
      <el-button @click="onClear">清空</el-button>
    </div>

    <div class="card table-card">
      <div class="table-header">
        <span class="muted">共 {{ total }} 条</span>
      </div>
      <el-table
        v-loading="loading"
        :data="items"
        class="weibo-table"
        empty-text="暂无微博数据"
      >
        <el-table-column label="时间" width="150">
          <template #default="{ row }">
            {{ row.created_at_ms ? formatTs(row.created_at_ms) : row.created_at_raw || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="360">
          <template #default="{ row }">
            <div class="post-text">{{ truncate(row.text, 140) }}</div>
            <div class="post-meta">
              {{ row.author_name || '未知作者' }}
              <span v-if="row.keywords.length"> · {{ row.keywords.join(' / ') }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="品牌" width="90">
          <template #default="{ row }">
            <el-tag>{{ labelFor(BRAND_FOCUS_LABEL, row.brand_focus) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="情绪" width="90">
          <template #default="{ row }">
            <el-tag :type="row.sentiment === 'negative' ? 'danger' : row.sentiment === 'positive' ? 'success' : 'info'">
              {{ labelFor(SENTIMENT_LABEL, row.sentiment) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险" width="110">
          <template #default="{ row }">
            <el-tag :type="row.risk_level === 'high' ? 'danger' : row.risk_level === 'watch' ? 'warning' : 'info'">
              {{ labelFor(RISK_LEVEL_LABEL, row.risk_level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="话题" min-width="160">
          <template #default="{ row }">
            <span class="topic-tags">
              <el-tag v-for="topic in row.topics" :key="topic" size="small" type="info">
                {{ labelFor(TOPIC_LABEL, topic) }}
              </el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="互动" width="120">
          <template #default="{ row }">
            <span class="muted">转{{ row.reposts_count ?? 0 }} 评{{ row.comments_count ?? 0 }} 赞{{ row.attitudes_count ?? 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="原文" width="80">
          <template #default="{ row }">
            <a :href="row.url" target="_blank" rel="noreferrer">打开</a>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
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
.weibo-list-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.filter-card {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.filter-input {
  width: 260px;
}
.filter-select {
  width: 150px;
}
.table-card {
  margin-top: 16px;
}
.table-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
}
.post-text {
  color: var(--text);
}
.post-meta {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 12px;
}
.topic-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
