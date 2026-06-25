import { reactive, computed, provide, inject, watch, type InjectionKey } from 'vue'
import {
  keywordSearch,
  smartSearch,
  fineFilterSSE,
  type ConversationItem,
  type KeywordGroup,
  type MetadataFilters,
  type FineFilterBatchResult,
} from '@/api/feedback'

// ---------------------------------------------------------------------------
// 搜索模式
// ---------------------------------------------------------------------------

export type SearchMode = 'none' | 'smart' | 'keyword'

// ---------------------------------------------------------------------------
// AI 相关性评分映射：conversation_id → score
// ---------------------------------------------------------------------------

export type AiScoreMap = Record<string, 1 | 2 | 3>

// ---------------------------------------------------------------------------
// 搜索状态
// ---------------------------------------------------------------------------

export interface SearchState {
  /** 当前激活的搜索模式 */
  mode: SearchMode
  /** AI 搜索查询文本 */
  smartQuery: string
  /** AI 搜索 loading */
  smartLoading: boolean
  /** 关键词构建器分组 */
  keywordGroups: KeywordGroup[]
  /** 关键词排除词 */
  keywordExcludes: string[]
  /** 关键词搜索 loading */
  keywordLoading: boolean
  /** 搜索结果 */
  items: ConversationItem[]
  /** 搜索结果总数 */
  total: number
  /** AI 相关性评分 */
  aiScores: AiScoreMap
  /** 精筛进行中 */
  fineFiltering: boolean
  /** 精筛进度 */
  fineFilterProgress: { done: number; total: number }
  /** 精筛 SSE 连接 */
  _fineFilterES: EventSource | null
}

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

const SEARCH_STATE_KEY: InjectionKey<ReturnType<typeof useSearchState>> =
  Symbol('searchState')
const STORAGE_KEY = 'feedback_hub.search_state.v1'

type SearchStateSnapshot = Pick<
  SearchState,
  'mode' | 'smartQuery' | 'keywordGroups' | 'keywordExcludes' | 'items' | 'total' | 'aiScores'
>

function canUseSessionStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined'
}

function readSnapshot(): Partial<SearchStateSnapshot> {
  if (!canUseSessionStorage()) return {}
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Partial<SearchStateSnapshot>
    return {
      mode: parsed.mode === 'smart' || parsed.mode === 'keyword' ? parsed.mode : 'none',
      smartQuery: typeof parsed.smartQuery === 'string' ? parsed.smartQuery : '',
      keywordGroups: Array.isArray(parsed.keywordGroups) && parsed.keywordGroups.length > 0
        ? parsed.keywordGroups
        : [{ keywords: [], logic: 'AND' }],
      keywordExcludes: Array.isArray(parsed.keywordExcludes) ? parsed.keywordExcludes : [],
      items: Array.isArray(parsed.items) ? parsed.items : [],
      total: typeof parsed.total === 'number' ? parsed.total : 0,
      aiScores: parsed.aiScores && typeof parsed.aiScores === 'object' ? parsed.aiScores : {},
    }
  } catch {
    return {}
  }
}

function writeSnapshot(snapshot: SearchStateSnapshot) {
  if (!canUseSessionStorage()) return
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
  } catch {
    // sessionStorage may be unavailable in private mode or full quota.
  }
}

export function provideSearchState() {
  const state = useSearchState()
  provide(SEARCH_STATE_KEY, state)
  return state
}

export function injectSearchState() {
  const state = inject(SEARCH_STATE_KEY)
  if (!state) throw new Error('SearchState not provided')
  return state
}

export function useSearchState() {
  const snapshot = readSnapshot()
  const state = reactive<SearchState>({
    mode: snapshot.mode ?? 'none',
    smartQuery: snapshot.smartQuery ?? '',
    smartLoading: false,
    keywordGroups: snapshot.keywordGroups ?? [{ keywords: [], logic: 'AND' }],
    keywordExcludes: snapshot.keywordExcludes ?? [],
    keywordLoading: false,
    items: snapshot.items ?? [],
    total: snapshot.total ?? 0,
    aiScores: snapshot.aiScores ?? {},
    fineFiltering: false,
    fineFilterProgress: { done: 0, total: 0 },
    _fineFilterES: null,
  })

  watch(
    () => ({
      mode: state.mode,
      smartQuery: state.smartQuery,
      keywordGroups: state.keywordGroups,
      keywordExcludes: state.keywordExcludes,
      items: state.items,
      total: state.total,
      aiScores: state.aiScores,
    }),
    writeSnapshot,
    { deep: true },
  )

  // ---- 是否有搜索条件 ----
  const hasSmartQuery = computed(() => state.smartQuery.trim().length > 0)
  const hasKeywordQuery = computed(() => {
    // 至少需要一组包含关键词，排除词是可选的附加过滤
    return state.keywordGroups.some(g => g.keywords.length > 0)
  })
  const hasAnySearch = computed(() => state.mode !== 'none')
  const hasSearchResults = computed(() => state.items.length > 0 && state.mode !== 'none')

  // ---- 当前搜索意图描述（用于精筛） ----
  const currentQueryIntent = computed(() => {
    if (state.mode === 'smart') return state.smartQuery
    if (state.mode === 'keyword') {
      const parts: string[] = []
      for (const g of state.keywordGroups) {
        if (g.keywords.length === 0) continue
        const joined = g.keywords.map(k => `'${k}'`).join(g.logic === 'AND' ? ' 和 ' : ' 或 ')
        parts.push(`包含${joined}`)
      }
      if (state.keywordExcludes.length > 0) {
        parts.push(`排除${state.keywordExcludes.map(k => `'${k}'`).join('、')}`)
      }
      return parts.join('，')
    }
    return ''
  })

  // ---- AI 搜索 ----
  async function doSmartSearch(filters: MetadataFilters) {
    if (!hasSmartQuery.value) return
    // 如果当前是关键词模式，清空关键词结果
    if (state.mode === 'keyword') {
      clearResults()
    }
    state.mode = 'smart'
    state.smartLoading = true
    clearAiScores()
    try {
      const r = await smartSearch({
        query: state.smartQuery,
        filters,
        limit: 200,
        offset: 0,
      })
      state.items = r.items
      state.total = r.total
    } finally {
      state.smartLoading = false
    }
  }

  // ---- 关键词搜索 ----
  async function doKeywordSearch(filters: MetadataFilters) {
    if (!hasKeywordQuery.value) return
    if (state.mode === 'smart') {
      clearResults()
    }
    state.mode = 'keyword'
    state.keywordLoading = true
    clearAiScores()
    try {
      const r = await keywordSearch({
        groups: state.keywordGroups,
        excludes: state.keywordExcludes,
        filters,
        limit: 200,
        offset: 0,
      })
      state.items = r.items
      state.total = r.total
    } finally {
      state.keywordLoading = false
    }
  }

  // ---- AI 精筛 ----
  function startFineFilter() {
    if (!hasSearchResults.value || !currentQueryIntent.value) return
    if (state.fineFiltering) return

    state.fineFiltering = true
    // 注意：clearAiScores() 会把 fineFilterProgress 重置为 {done:0,total:0}，
    // 因此必须先清空、再设置 total，否则分母会被清回 0。
    clearAiScores()
    state.fineFilterProgress = { done: 0, total: state.items.length }

    const ids = state.items.map(it => it.conversation_id)
    const es = fineFilterSSE(
      {
        query: currentQueryIntent.value,
        conversation_ids: ids,
        // 不传 batch_size，让后端自适应（≤50条时 batch=1，全并发）
      },
      // onBatch
      (results: FineFilterBatchResult[], processed: number) => {
        for (const r of results) {
          if (r.score) {
            state.aiScores[r.id] = r.score
          }
        }
        state.fineFilterProgress.done = processed
      },
      // onDone
      () => {
        state.fineFiltering = false
        state._fineFilterES = null
      },
      // onError
      () => {
        state.fineFiltering = false
        state._fineFilterES = null
      },
      // onWarning
      (message: string) => {
        console.warn('Fine filter warning:', message)
        // 可选：后续可以加一个 state.fineFilterWarning 来展示给用户
      },
    )
    state._fineFilterES = es
  }

  function cancelFineFilter() {
    if (state._fineFilterES) {
      state._fineFilterES.close()
      state._fineFilterES = null
    }
    state.fineFiltering = false
  }

  // ---- 清理 ----
  function clearResults() {
    state.items = []
    state.total = 0
    state.mode = 'none'
    clearAiScores()
  }

  function clearAiScores() {
    state.aiScores = {}
    state.fineFilterProgress = { done: 0, total: 0 }
  }

  function clearSmartQuery() {
    state.smartQuery = ''
    if (state.mode === 'smart') clearResults()
  }

  function clearKeywordQuery() {
    state.keywordGroups = [{ keywords: [], logic: 'AND' }]
    state.keywordExcludes = []
    if (state.mode === 'keyword') clearResults()
  }

  function clearAllSearch() {
    clearSmartQuery()
    clearKeywordQuery()
    clearResults()
  }

  return {
    state,
    hasSmartQuery,
    hasKeywordQuery,
    hasAnySearch,
    hasSearchResults,
    currentQueryIntent,
    doSmartSearch,
    doKeywordSearch,
    startFineFilter,
    cancelFineFilter,
    clearResults,
    clearSmartQuery,
    clearKeywordQuery,
    clearAllSearch,
  }
}
