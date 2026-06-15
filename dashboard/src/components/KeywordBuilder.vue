<script setup lang="ts">
import { computed } from 'vue'
import { injectSearchState } from '@/composables/useSearchState'
import type { MetadataFilters } from '@/api/feedback'
import KeywordGroup from './KeywordGroup.vue'
import ExcludeSection from './ExcludeSection.vue'

const props = defineProps<{ filters: MetadataFilters }>()

const {
  state,
  hasKeywordQuery,
  doKeywordSearch,
} = injectSearchState()

const newKeywords = defineModel<Record<number, string>>('newKeywords', {
  default: () => ({}),
})

// 排除词输入框里「尚未回车」的残留文本（提升到此处以便搜索时兜底提交）
const newExclude = defineModel<string>('newExclude', { default: '' })

// 是否存在「已输入但未提交」的关键词残留文本
const hasPendingKeyword = computed(() =>
  Object.values(newKeywords.value).some(v => (v || '').trim().length > 0)
)

// 按钮可用：已有关键词标签，或输入框里有待提交的关键词文本
// （排除词是可选附加过滤，单独的排除词残留不足以触发搜索）
const canSearch = computed(() => hasKeywordQuery.value || hasPendingKeyword.value)

function addGroup() {
  state.keywordGroups = [
    ...state.keywordGroups,
    { keywords: [], logic: 'OR' },
  ]
}

function removeGroup(index: number) {
  if (state.keywordGroups.length <= 1) return
  const next = [...state.keywordGroups]
  next.splice(index, 1)
  state.keywordGroups = next
}

function toggleGroupLogic(index: number) {
  const next = [...state.keywordGroups]
  next[index] = {
    ...next[index],
    logic: next[index].logic === 'AND' ? 'OR' : 'AND',
  }
  state.keywordGroups = next
}

function addKeyword(groupIndex: number, keyword: string) {
  const next = [...state.keywordGroups]
  next[groupIndex] = {
    ...next[groupIndex],
    keywords: [...next[groupIndex].keywords, keyword],
  }
  state.keywordGroups = next
}

function removeKeyword(groupIndex: number, keywordIndex: number) {
  const next = [...state.keywordGroups]
  const kws = [...next[groupIndex].keywords]
  kws.splice(keywordIndex, 1)
  next[groupIndex] = { ...next[groupIndex], keywords: kws }
  state.keywordGroups = next
}

/**
 * 搜索兜底：把各输入框里「尚未回车」的残留文本自动提交为标签，
 * 这样无论用户是否回车，点搜索都能拿到预期结果。
 */
function flushPendingInputs() {
  // 关键词残留
  const groups = state.keywordGroups.map(g => ({ ...g, keywords: [...g.keywords] }))
  let changed = false
  for (const [k, v] of Object.entries(newKeywords.value)) {
    const gi = Number(k)
    const t = (v || '').trim()
    if (t && groups[gi]) {
      groups[gi].keywords = [...groups[gi].keywords, t]
      changed = true
    }
  }
  if (changed) state.keywordGroups = groups
  newKeywords.value = {}

  // 排除词残留
  const ex = newExclude.value.trim()
  if (ex) {
    state.keywordExcludes = [...state.keywordExcludes, ex]
    newExclude.value = ''
  }
}

function onSearch() {
  flushPendingInputs()
  if (!hasKeywordQuery.value) return
  void doKeywordSearch(props.filters)
}
</script>

<template>
  <div class="keyword-builder">
    <div class="groups-container">
      <template
        v-for="(group, gi) in state.keywordGroups"
        :key="gi"
      >
        <div
          v-if="gi > 0"
          class="group-separator"
        >
          OR
        </div>
        <KeywordGroup
          :group="group"
          :index="gi"
          :can-remove="state.keywordGroups.length > 1"
          :new-keyword="newKeywords[gi] || ''"
          @update:new-keyword="(v: string) => { newKeywords = { ...newKeywords, [gi]: v } }"
          @remove="removeGroup(gi)"
          @toggle-logic="toggleGroupLogic(gi)"
          @add-keyword="(kw: string) => addKeyword(gi, kw)"
          @remove-keyword="(ki: number) => removeKeyword(gi, ki)"
        />
      </template>
    </div>

    <ExcludeSection
      v-model="state.keywordExcludes"
      v-model:new-exclude="newExclude"
    />

    <div class="builder-actions">
      <el-button
        size="small"
        @click="addGroup"
      >
        + 添加分组
      </el-button>
      <el-button
        type="primary"
        :loading="state.keywordLoading"
        :disabled="!canSearch || state.smartLoading"
        @click="onSearch"
      >
        搜索
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.keyword-builder {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.groups-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.group-separator {
  text-align: center;
  font-weight: 700;
  font-size: 13px;
  color: var(--el-color-primary);
  padding: 4px 0;
}
.builder-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
