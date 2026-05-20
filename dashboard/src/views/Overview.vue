<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import KpiCard from '@/components/KpiCard.vue'
import ChartPie from '@/components/ChartPie.vue'
import ChartBar from '@/components/ChartBar.vue'
import ChartTrend from '@/components/ChartTrend.vue'
import ConversationTable from '@/components/ConversationTable.vue'
import {
  getDistribution,
  getTrend,
  listConversations,
  type DistributionResp,
  type TrendResp,
  type ConversationItem,
} from '@/api/feedback'
import { L1_COLOR, SEVERITY_COLOR } from '@/constants/labels'
import { formatTsShort, formatPercent } from '@/utils/format'

const router = useRouter()

// 时间窗
const days = ref<7 | 14 | 30>(7)
const range = computed(() => ({
  from: dayjs().subtract(days.value, 'day').format('YYYY-MM-DD'),
  to: dayjs().format('YYYY-MM-DD'),
}))

// 数据状态
const loadingTopBlock = ref(false)
const distribution = ref<DistributionResp>({ L1: {}, L2: {}, severity: {} })
const trend = ref<TrendResp>({ granularity: 'day', buckets: [] })
const latestItems = ref<ConversationItem[]>([])
const pendingItems = ref<ConversationItem[]>([])
const loadingLatest = ref(false)
const loadingPending = ref(false)

// KPI 计算
const kpiTotal = computed(
  () => Object.values(distribution.value.L1).reduce((a, b) => a + b, 0),
)
const kpiAutoRate = computed(() => {
  const total = kpiTotal.value
  if (total === 0) return Number.NaN
  const pending = distribution.value.L1['待定'] ?? 0
  return (total - pending) / total
})
const kpiP0 = computed(() => distribution.value.severity['P0'] ?? 0)
const kpiPendingRate = computed(() => {
  const total = kpiTotal.value
  if (total === 0) return Number.NaN
  return (distribution.value.L1['待定'] ?? 0) / total
})
const kpiLatestTs = computed(() => latestItems.value[0]?.last_ts_ms ?? null)

// 颜色映射
const l1ColorMap = Object.fromEntries(
  Object.entries(L1_COLOR).map(([k, v]) => [k, v.fg]),
) as Record<string, string>
const sevColorMap = Object.fromEntries(
  Object.entries(SEVERITY_COLOR).map(([k, v]) => [k, v.fg]),
) as Record<string, string>

// 加载顶部数据块（KPI + 三图）
async function loadTopBlock() {
  loadingTopBlock.value = true
  try {
    const [d, t] = await Promise.all([
      getDistribution(range.value),
      getTrend({ ...range.value, granularity: 'day' }),
    ])
    distribution.value = d
    trend.value = t
  } finally {
    loadingTopBlock.value = false
  }
}

// 加载"最新 Top 10"
async function loadLatest() {
  loadingLatest.value = true
  try {
    const r = await listConversations({ limit: 10, offset: 0 })
    latestItems.value = r.items
  } finally {
    loadingLatest.value = false
  }
}

// 加载"待定 Top 10"
async function loadPending() {
  loadingPending.value = true
  try {
    const r = await listConversations({ L1: '待定', limit: 10, offset: 0 })
    pendingItems.value = r.items
  } finally {
    loadingPending.value = false
  }
}

watch(days, loadTopBlock)

onMounted(() => {
  void loadTopBlock()
  void loadLatest()
  void loadPending()
})

function gotoListAll() {
  router.push('/list')
}
function gotoListPending() {
  router.push('/list?L1=待定')
}
</script>

<template>
  <div class="page">
    <div class="overview-header">
      <h1 class="page-title">
        概览
      </h1>
      <el-radio-group
        v-model="days"
        size="default"
      >
        <el-radio-button :value="7">
          近 7 天
        </el-radio-button>
        <el-radio-button :value="14">
          近 14 天
        </el-radio-button>
        <el-radio-button :value="30">
          近 30 天
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- KPI 卡片 -->
    <div
      v-loading="loadingTopBlock"
      class="kpi-grid"
    >
      <KpiCard
        title="总会话"
        :value="kpiTotal"
      />
      <KpiCard
        title="自动定级率"
        :value="formatPercent(kpiAutoRate)"
        tooltip="非待定占比 = (总会话 - 待定数) / 总会话"
      />
      <KpiCard
        title="P0 数"
        :value="kpiP0"
      />
      <KpiCard
        title="待定占比"
        :value="formatPercent(kpiPendingRate)"
        tooltip="L1=待定 的占比"
      />
      <KpiCard
        title="最近反馈"
        :value="kpiLatestTs ? formatTsShort(kpiLatestTs) : '—'"
      />
    </div>

    <!-- 趋势图 -->
    <div
      v-loading="loadingTopBlock"
      class="card chart-card"
    >
      <div class="chart-title">
        会话量趋势（按 L1 堆叠）
      </div>
      <ChartTrend :buckets="trend.buckets" />
    </div>

    <!-- 三张分布图 -->
    <div
      v-loading="loadingTopBlock"
      class="dist-grid"
    >
      <div class="card chart-card">
        <div class="chart-title">
          L1 分布
        </div>
        <ChartPie
          :data="distribution.L1"
          :color-map="l1ColorMap"
        />
      </div>
      <div class="card chart-card">
        <div class="chart-title">
          L2 Top 10
        </div>
        <ChartBar
          :data="distribution.L2"
          :top-n="10"
        />
      </div>
      <div class="card chart-card">
        <div class="chart-title">
          severity 分布
        </div>
        <ChartPie
          :data="distribution.severity"
          :color-map="sevColorMap"
        />
      </div>
    </div>

    <!-- 最新 Top 10 -->
    <div class="card list-card">
      <div class="chart-title">
        <span>最新反馈 · Top 10</span>
        <el-button
          link
          type="primary"
          @click="gotoListAll"
        >
          查看全部 →
        </el-button>
      </div>
      <ConversationTable
        :items="latestItems"
        :loading="loadingLatest"
      />
    </div>

    <!-- 待定 Top 10 -->
    <div class="card list-card">
      <div class="chart-title">
        <span>待定桶 · Top 10</span>
        <el-button
          link
          type="primary"
          @click="gotoListPending"
        >
          查看全部 →
        </el-button>
      </div>
      <ConversationTable
        :items="pendingItems"
        :loading="loadingPending"
        empty-text="当前没有待定反馈 ✅"
      />
    </div>
  </div>
</template>

<style scoped>
.overview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.overview-header .page-title {
  margin: 0;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.dist-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 16px;
}
.chart-card {
  margin-top: 16px;
}
.list-card {
  margin-top: 16px;
}
.chart-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
@media (max-width: 1280px) {
  .kpi-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .dist-grid {
    grid-template-columns: repeat(1, 1fr);
  }
}
</style>
