import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { getUntaggedSourceCount, listSourceTagOptions, type SourceTag } from '../../../lib/sourceApi'

export const SOURCE_TAG_FILTER_LIMIT = 50

/** Keep the filter catalog authoritative without accepting stale requests. */
export function useSourceTagFilter(selectedIds: Ref<number[]>, draftIds: Ref<number[]>, open: Ref<boolean>) {
  const availableSourceTags = ref<SourceTag[]>([])
  const untaggedSourceCount = ref<number | null>(null)
  const tagFilterLoading = ref(false)
  const tagFilterError = ref(false)
  let controller: AbortController | null = null
  let countController: AbortController | null = null
  let requestId = 0
  let disposed = false

  function setTagFilterDraftIds(ids: number[]) {
    if (tagFilterLoading.value || tagFilterError.value || ids.length > SOURCE_TAG_FILTER_LIMIT) return
    draftIds.value = [...ids]
  }

  function applyTagFilterIds(): boolean {
    if (tagFilterLoading.value || tagFilterError.value || draftIds.value.length > SOURCE_TAG_FILTER_LIMIT) return false
    selectedIds.value = [...draftIds.value]
    return true
  }

  async function refreshTagFilterOptions() {
    if (disposed || (!open.value && !selectedIds.value.length)) return
    if (open.value) void refreshUntaggedCount()
    const request = ++requestId
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal
    tagFilterLoading.value = true
    tagFilterError.value = false
    try {
      const tags = await listSourceTagOptions('filter', { signal })
      if (disposed || request !== requestId) return
      availableSourceTags.value = tags
      const validIds = new Set(tags.map((tag) => tag.id))
      const selected = selectedIds.value.filter((id) => validIds.has(id))
      // Avoid triggering source reloads when the selection did not change.
      if (selected.length !== selectedIds.value.length) selectedIds.value = selected
      draftIds.value = draftIds.value.filter((id) => validIds.has(id))
    } catch {
      if (!disposed && request === requestId && !signal.aborted) tagFilterError.value = true
    } finally {
      if (!disposed && request === requestId) tagFilterLoading.value = false
    }
  }

  async function refreshUntaggedCount() {
    countController?.abort()
    countController = new AbortController()
    const { signal } = countController
    try {
      const count = await getUntaggedSourceCount({ signal })
      if (!signal.aborted) untaggedSourceCount.value = count
    } catch {
      if (!signal.aborted) untaggedSourceCount.value = null
    } finally {
      if (countController?.signal === signal) countController = null
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') onFocus()
  }
  function onFocus() {
    if (!tagFilterLoading.value && !countController) void refreshTagFilterOptions()
  }

  watch(open, (visible) => {
    if (visible) void refreshTagFilterOptions()
    else {
      countController?.abort()
      countController = null
      untaggedSourceCount.value = null
    }
  })
  onMounted(() => {
    void refreshTagFilterOptions()
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })
  onUnmounted(() => {
    disposed = true
    requestId += 1
    controller?.abort()
    countController?.abort()
    window.removeEventListener('focus', onFocus)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    availableSourceTags, untaggedSourceCount, tagFilterLoading, tagFilterError,
    refreshTagFilterOptions, setTagFilterDraftIds, applyTagFilterIds,
  }
}
