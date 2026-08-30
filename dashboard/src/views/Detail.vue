<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import L1Tag from '@/components/L1Tag.vue'
import SeverityTag from '@/components/SeverityTag.vue'
import { getConversation, type DetailResp } from '@/api/feedback'
import { formatTs } from '@/utils/format'

const props = defineProps<{ id: string }>()
const router = useRouter()
const route = useRoute()

const data = ref<DetailResp | null>(null)
const notFound = ref(false)
const loading = ref(false)

async function load() {
  loading.value = true
  notFound.value = false
  try {
    data.value = await getConversation(props.id)
  } catch (e) {
    if ((e as { type?: string })?.type === 'NOT_FOUND') {
      notFound.value = true
    } else {
      ElMessage.error('加载失败')
    }
  } finally {
    loading.value = false
  }
}

function goBack() {
  const from = typeof route.query.from === 'string' ? route.query.from : ''
  router.push(from.startsWith('/') ? from : '/')
}

function openOriginalChat() {
  const url = data.value?.conversation.external_chat_url
  if (!url) {
    ElMessage.error('缺少原始会话链接')
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

onMounted(load)
</script>

<template>
  <div
    v-loading="loading"
    class="page"
  >
    <div class="detail-header">
      <el-button @click="goBack">
        ← 返回列表
      </el-button>
      <el-button
        v-if="data"
        type="primary"
        :disabled="!data.conversation.external_chat_url"
        @click="openOriginalChat"
      >
        打开原始会话
      </el-button>
    </div>

    <div
      v-if="notFound"
      class="card empty-state"
    >
      <h2>反馈不存在</h2>
      <p class="muted">
        可能已被删除或 ID 错误。
      </p>
      <el-button
        type="primary"
        @click="goBack"
      >
        返回列表
      </el-button>
    </div>

    <template v-else-if="data">
      <!-- 会话头信息 -->
      <div class="card conv-head">
        <div class="conv-id font-mono muted">
          会话 ID: {{ data.conversation.conversation_id }}
        </div>
        <div class="conv-time">
          时间范围：
          <span class="font-mono">{{ formatTs(data.conversation.first_ts_ms) }}</span>
          →
          <span class="font-mono">{{ formatTs(data.conversation.last_ts_ms) }}</span>
          <span class="muted">（共 {{ data.conversation.msg_count }} 条消息）</span>
        </div>
        <el-divider />
        <div class="tag-row">
          <L1Tag :value="data.conversation.L1" />
          <span
            v-for="l2 in data.conversation.L2"
            :key="l2"
            class="l2-pill"
          >{{ l2 }}</span>
          <SeverityTag :value="data.conversation.severity" />
          <span class="muted">置信度：{{ (data.conversation.confidence ?? 0).toFixed(2) }}</span>
        </div>
        <div
          v-if="data.conversation.reason"
          class="reason muted"
        >
          理由：{{ data.conversation.reason }}
        </div>
        <el-divider />
        <div class="meta">
          <span>用户：{{ data.conversation.user_vid ?? '—' }}</span>
          <span>版本：{{ data.conversation.appversion ?? '—' }}</span>
          <span>渠道：{{ data.conversation.channel }}</span>
        </div>
      </div>

      <!-- 消息时间线 -->
      <div class="card timeline">
        <div class="chart-title">
          消息时间线
        </div>
        <div
          v-for="m in data.messages"
          :key="m.feedback_id"
          class="msg"
        >
          <div class="msg-head">
            <span class="msg-seq">#{{ m.msg_seq }}</span>
            <span class="font-mono muted">{{ formatTs(m.ts_ms) }}</span>
          </div>
          <div class="msg-text">
            {{ m.text }}
          </div>
          <div class="msg-label muted">
            <span>打标来源：<b>{{ m.source ?? '—' }}</b></span>
            <span v-if="m.rule_name"> · rule={{ m.rule_name }}</span>
            <span> · L1=</span>
            <L1Tag :value="m.L1" />
            <span v-if="m.L2.length"> · L2={{ m.L2.join(' / ') }}</span>
            <span> · </span>
            <SeverityTag :value="m.severity" />
            <span v-if="m.confidence != null"> · conf={{ m.confidence.toFixed(2) }}</span>
          </div>
          <div
            v-if="m.reason"
            class="msg-reason muted"
          >
            理由：{{ m.reason }}
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}
.empty-state {
  text-align: center;
  padding: 60px 24px;
}
.empty-state h2 {
  margin: 0 0 8px;
}
.empty-state p {
  margin: 0 0 24px;
}

.conv-head {
  margin-bottom: 16px;
}
.conv-id {
  font-size: 12px;
  margin-bottom: 8px;
}
.conv-time {
  font-size: 14px;
}
.tag-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.l2-pill {
  padding: 2px 8px;
  border-radius: 4px;
  background: #e0f2fe;
  color: #0369a1;
  font-size: 12px;
}
.reason {
  margin-top: 8px;
  font-size: 13px;
}
.meta {
  display: flex;
  gap: 24px;
  font-size: 13px;
  color: var(--text-muted);
}

.timeline .chart-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
}
.msg {
  border-left: 2px solid var(--border);
  padding: 8px 16px 16px 16px;
  margin-bottom: 8px;
}
.msg-head {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 6px;
}
.msg-seq {
  font-weight: 600;
  color: var(--primary);
}
.msg-text {
  white-space: pre-wrap;
  background: var(--bg);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
}
.msg-label {
  font-size: 13px;
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: wrap;
}
.msg-reason {
  font-size: 13px;
  margin-top: 4px;
}
</style>
