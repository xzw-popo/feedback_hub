<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import KpiCard from '@/components/KpiCard.vue'
import { getWeiboStats, type WeiboStatsResp } from '@/api/weibo'
import { BRAND_FOCUS_LABEL, RISK_LEVEL_LABEL, SENTIMENT_LABEL, TOPIC_LABEL, labelFor } from '@/constants/weibo'
import { formatPercent, formatTsShort, truncate } from '@/utils/format'

const router = useRouter()
const days = ref<7 | 14 | 30>(7)
const loading = ref(false)
const stats = ref<WeiboStatsResp>({
  total_posts: 0,
  brand_focus_counts: {},
  sentiment_counts: {},
  topic_counts: {},
  risk_counts: {},
  trend: [],
  latest_crawl_run: null,
  high_risk_posts: [],
})

const range = computed(() => ({
  from: dayjs().subtract(days.value, 'day').format('YYYY-MM-DD'),
  to: dayjs().format('YYYY-MM-DD'),
}))
const negativeRate = computed(() => {
  const total = stats.value.total_posts
  if (!total) return Number.NaN
  return (stats.value.sentiment_counts.negative ?? 0) / total
})
const latestRunText = computed(() => {
  const run = stats.value.latest_crawl_run
  if (!run) return '暂无'
  return `${run.status} · ${formatTsShort(run.started_at * 1000)}`
})
const topTopics = computed(() =>
  Object.entries(stats.value.topic_counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8),
)

async function load() {
  loading.value = true
  try {
    stats.value = await getWeiboStats(range.value)
  } finally {
    loading.value = false
  }
}

function gotoList(params: Record<string, string>) {
  router.push({ path: '/weibo/list', query: params })
}

watch(days, load)
onMounted(() => { void load() })
</script>

<template>
  <div class="page weibo-page">
    <div class="weibo-header">
      <div>
        <h1 class="page-title">微博舆情</h1>
        <div class="muted">公开微博反馈中的豆包、微信和对比讨论</div>
      </div>
      <el-radio-group v-model="days">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="14">近 14 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
      </el-radio-group>
    </div>

    <div v-loading="loading" class="kpi-grid weibo-kpis">
      <KpiCard title="微博总量" :value="stats.total_posts" />
      <KpiCard title="豆包相关" :value="stats.brand_focus_counts.doubao ?? 0" />
      <KpiCard title="微信相关" :value="stats.brand_focus_counts.wechat ?? 0" />
      <KpiCard title="对比讨论" :value="stats.brand_focus_counts.comparison ?? 0" />
      <KpiCard title="负向占比" :value="formatPercent(negativeRate)" />
      <KpiCard title="最近采集" :value="latestRunText" />
    </div>

    <div v-loading="loading" class="weibo-grid">
      <section class="card">
        <div class="section-title">品牌焦点</div>
        <button
          v-for="[key, count] in Object.entries(stats.brand_focus_counts)"
          :key="key"
          class="metric-row"
          type="button"
          @click="gotoList({ brand_focus: key })"
        >
          <span>{{ labelFor(BRAND_FOCUS_LABEL, key) }}</span>
          <strong>{{ count }}</strong>
        </button>
      </section>

      <section class="card">
        <div class="section-title">情绪立场</div>
        <button
          v-for="[key, count] in Object.entries(stats.sentiment_counts)"
          :key="key"
          class="metric-row"
          type="button"
          @click="gotoList({ sentiment: key })"
        >
          <span>{{ labelFor(SENTIMENT_LABEL, key) }}</span>
          <strong>{{ count }}</strong>
        </button>
      </section>

      <section class="card">
        <div class="section-title">风险级别</div>
        <button
          v-for="[key, count] in Object.entries(stats.risk_counts)"
          :key="key"
          class="metric-row"
          type="button"
          @click="gotoList({ risk_level: key })"
        >
          <span>{{ labelFor(RISK_LEVEL_LABEL, key) }}</span>
          <strong>{{ count }}</strong>
        </button>
      </section>
    </div>

    <div v-loading="loading" class="weibo-grid wide">
      <section class="card">
        <div class="section-title">热门话题</div>
        <div class="topic-list">
          <button
            v-for="[key, count] in topTopics"
            :key="key"
            class="topic-pill"
            type="button"
            @click="gotoList({ topic: key })"
          >
            {{ labelFor(TOPIC_LABEL, key) }} <span>{{ count }}</span>
          </button>
        </div>
      </section>

      <section class="card">
        <div class="section-title">高风险微博</div>
        <div v-if="stats.high_risk_posts.length === 0" class="empty-state">暂无高风险内容</div>
        <div v-for="post in stats.high_risk_posts" :key="post.weibo_id" class="risk-post">
          <div class="risk-post-main">{{ truncate(post.text, 96) }}</div>
          <a :href="post.url" target="_blank" rel="noreferrer">原文</a>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.weibo-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}
.weibo-kpis {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}
.weibo-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.weibo-grid.wide {
  grid-template-columns: 1fr 1fr;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}
.metric-row {
  width: 100%;
  border: 0;
  border-top: 1px solid var(--border);
  background: transparent;
  padding: 10px 0;
  display: flex;
  justify-content: space-between;
  color: var(--text);
  cursor: pointer;
}
.metric-row:hover {
  color: var(--primary);
}
.topic-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.topic-pill {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  padding: 6px 10px;
  color: var(--text);
  cursor: pointer;
}
.topic-pill span {
  color: var(--text-muted);
}
.risk-post {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  padding: 10px 0;
  border-top: 1px solid var(--border);
}
.risk-post-main {
  min-width: 0;
}
.empty-state {
  color: var(--text-muted);
  padding: 24px 0;
}
@media (max-width: 1100px) {
  .weibo-kpis,
  .weibo-grid,
  .weibo-grid.wide {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

