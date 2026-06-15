<script setup lang="ts">
const excludes = defineModel<string[]>({ required: true })
const newExclude = defineModel<string>('newExclude', { default: '' })

/** 提交当前输入框里的文本为一个排除词标签（回车 / 失焦 共用）。 */
function commit() {
  const t = newExclude.value.trim()
  if (t) {
    excludes.value = [...excludes.value, t]
    newExclude.value = ''
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') commit()
}

function removeExclude(index: number) {
  const next = [...excludes.value]
  next.splice(index, 1)
  excludes.value = next
}
</script>

<template>
  <div class="exclude-section">
    <span class="exclude-label">排除</span>
    <el-tag
      v-for="(ex, i) in excludes"
      :key="i"
      closable
      type="danger"
      size="default"
      @close="removeExclude(i)"
    >
      {{ ex }}
    </el-tag>
    <el-input
      v-model="newExclude"
      placeholder="输入排除词，回车添加"
      size="small"
      style="width: 150px"
      @keydown="onKeydown"
      @blur="commit"
    />
  </div>
</template>

<style scoped>
.exclude-section {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  padding: 8px 12px;
  border: 1px dashed var(--el-color-danger-light-5);
  border-radius: 6px;
  background: var(--el-color-danger-light-9);
}
.exclude-label {
  font-weight: 600;
  font-size: 13px;
  color: var(--el-color-danger);
  margin-right: 4px;
}
</style>
