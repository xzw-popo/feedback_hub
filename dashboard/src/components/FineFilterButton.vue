<script setup lang="ts">
import { injectSearchState } from '@/composables/useSearchState'

const {
  state,
  hasSearchResults,
  currentQueryIntent,
  startFineFilter,
  cancelFineFilter,
} = injectSearchState()

function onClick() {
  if (state.fineFiltering) {
    cancelFineFilter()
  } else {
    startFineFilter()
  }
}
</script>

<template>
  <el-button
    v-if="hasSearchResults && currentQueryIntent"
    :type="state.fineFiltering ? 'warning' : 'primary'"
    plain
    @click="onClick"
  >
    <template v-if="state.fineFiltering">
      ✨ 精筛中... {{ state.fineFilterProgress.done }}/{{ state.fineFilterProgress.total }}
    </template>
    <template v-else-if="Object.keys(state.aiScores).length > 0">
      ✨ 重新精筛
    </template>
    <template v-else>
      ✨ AI 精筛
    </template>
  </el-button>
</template>
