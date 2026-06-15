<script setup lang="ts">
import type { KeywordGroup } from '@/api/feedback'

const props = defineProps<{
  group: KeywordGroup
  index: number
  canRemove: boolean
}>()

const emit = defineEmits<{
  (e: 'remove'): void
  (e: 'toggle-logic'): void
  (e: 'add-keyword', keyword: string): void
  (e: 'remove-keyword', index: number): void
}>()

const newKeyword = defineModel<string>('newKeyword', { default: '' })

/** 提交当前输入框里的文本为一个关键词标签（回车 / 失焦 共用）。 */
function commit() {
  const t = newKeyword.value.trim()
  if (t) {
    emit('add-keyword', t)
    newKeyword.value = ''
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') commit()
}
</script>

<template>
  <div class="keyword-group">
    <div class="group-header">
      <span class="group-label">组 {{ index + 1 }}</span>
      <el-select
        :model-value="group.logic"
        size="small"
        style="width: 80px"
        @update:model-value="emit('toggle-logic')"
      >
        <el-option label="AND" value="AND" />
        <el-option label="OR" value="OR" />
      </el-select>
      <el-button
        v-if="canRemove"
        type="danger"
        text
        size="small"
        @click="emit('remove')"
      >
        删除组
      </el-button>
    </div>
    <div class="group-keywords">
      <el-tag
        v-for="(kw, ki) in group.keywords"
        :key="ki"
        closable
        type="success"
        size="default"
        @close="emit('remove-keyword', ki)"
      >
        {{ kw }}
      </el-tag>
      <el-input
        v-model="newKeyword"
        placeholder="输入关键词，回车添加"
        size="small"
        style="width: 160px"
        @keydown="onKeydown"
        @blur="commit"
      />
    </div>
  </div>
</template>

<style scoped>
.keyword-group {
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-bg-color);
}
.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.group-label {
  font-weight: 600;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.group-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
</style>
