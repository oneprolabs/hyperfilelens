import { computed, ref, watch, type Ref } from 'vue'
import type { ComposerTranslation } from 'vue-i18n'
import type { ErrorDetailsPayload } from '../lib/errors/details'
import { asyncNoticeScope } from '../lib/asyncNoticeLedger'
import { latestUnregisterFailure } from '../lib/sourceFailureDiagnostics'
import { previousUnregisterFailureDetails, unregisterFailureToErrorDetails } from '../lib/unregisterFailureDetails'

/** Dialog-local read-only history, discarded on close/selection change. */
export function useUnregisterFailureHistory(
  t: ComposerTranslation,
  visible: Ref<boolean>,
  sourceIds: Ref<string[]>,
  sourceName: (sourceId: string) => string,
) {
  const failures = ref<ErrorDetailsPayload[]>([])
  const loading = ref(false)
  const failed = ref(false)
  const revision = ref(0)
  watch(
    [() => visible.value, () => sourceIds.value.join(','), asyncNoticeScope, () => revision.value],
    async ([open, , scope], _previous, onCleanup) => {
      const controller = new AbortController()
      onCleanup(() => controller.abort())
      failures.value = []
      failed.value = false
      loading.value = false
      if (!open || !sourceIds.value.length) return
      loading.value = true
      const ids = [...new Set(sourceIds.value)]
      const results = new Map<string, ErrorDetailsPayload>()
      let index = 0
      let hasError = false
      const current = () => !controller.signal.aborted && asyncNoticeScope() === scope
      const worker = async () => {
        while (index < ids.length && current()) {
          const sourceId = ids[index++]!
          try {
            const task = await latestUnregisterFailure(sourceId, { signal: controller.signal })
            if (task && current()) results.set(sourceId, unregisterFailureToErrorDetails({
              t, sourceId, sourceName: sourceName(sourceId), task,
            }))
          } catch {
            if (current()) hasError = true
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, ids.length) }, worker))
      if (controller.signal.aborted) return
      if (asyncNoticeScope() !== scope) {
        loading.value = false
        return
      }
      failures.value = ids.flatMap(id => results.has(id) ? [results.get(id)!] : [])
      failed.value = hasError
      loading.value = false
    },
    { immediate: true },
  )
  return {
    details: computed(() => previousUnregisterFailureDetails(t, failures.value)),
    loading,
    failed,
    reload: () => { revision.value += 1 },
  }
}
