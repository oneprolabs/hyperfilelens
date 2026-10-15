import { onScopeDispose, ref } from 'vue'
import type { ComposerTranslation } from 'vue-i18n'
import { openErrorDetails } from '../lib/errors/details'
import { isAbortError } from '../lib/errors/normalizer'
import { notifyError } from '../lib/notify'
import { asyncNoticeScope } from '../lib/asyncNoticeLedger'
import { resolveSourceFailureDetails } from '../lib/sourceFailureDiagnostics'

export function useSourceFailureDetails(t: ComposerTranslation) {
  const loading = ref(new Set<string>())
  const controllers = new Map<string, AbortController>()
  onScopeDispose(() => {
    controllers.forEach(controller => controller.abort())
    controllers.clear()
  })

  async function open(sourceId: string, sourceName: string, status?: string) {
    if (loading.value.has(sourceId)) return
    const scope = asyncNoticeScope()
    const controller = new AbortController()
    controllers.set(sourceId, controller)
    loading.value = new Set([...loading.value, sourceId])
    try {
      const details = await resolveSourceFailureDetails({
        t, sourceId, sourceName, status, init: { signal: controller.signal },
      })
      if (!controller.signal.aborted && asyncNoticeScope() === scope) openErrorDetails(details)
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error) && asyncNoticeScope() === scope) {
        notifyError({
          error,
          message: t('protection.backupsPage.sourceFailureDetailsLoadFailed'),
          showDetails: true,
        })
      }
    } finally {
      controllers.delete(sourceId)
      const next = new Set(loading.value)
      next.delete(sourceId)
      loading.value = next
    }
  }

  return { open, isLoading: (sourceId: string) => loading.value.has(sourceId) }
}
