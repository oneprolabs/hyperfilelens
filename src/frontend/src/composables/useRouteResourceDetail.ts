import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage, isAbortError } from '../lib/api'
import { notifyError } from '../lib/notify'

/** Resolve deep links independently of list pagination and cancel stale opens. */
export function useRouteResourceDetail<T>(options: {
  queryKey: string
  enabled: () => boolean
  load: (id: number, signal: AbortSignal) => Promise<T>
  open: (resource: T) => void
  reset?: () => void
  queryForResource?: (resource: T) => Record<string, string>
}) {
  const route = useRoute()
  const router = useRouter()
  const loading = ref(false)

  watch(
    () => [route.fullPath, options.enabled()] as const,
    async (_, __, onCleanup) => {
      loading.value = false
      const raw = route.query[options.queryKey]
      if (!options.enabled() || typeof raw !== 'string' || !/^[1-9]\d*$/.test(raw)) return
      const id = Number(raw)
      if (!Number.isSafeInteger(id)) return
      options.reset?.()
      const controller = new AbortController()
      onCleanup(() => controller.abort())
      loading.value = true
      let resourceQuery: Record<string, string> = {}
      try {
        const resource = await options.load(id, controller.signal)
        if (controller.signal.aborted) return
        resourceQuery = options.queryForResource?.(resource) ?? {}
        options.open(resource)
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return
        if ((error as { status?: number })?.status !== 404) {
          notifyError({
            message: apiErrorMessage(error),
            error,
            showDetails: true,
            dedupeKey: `route-detail:${options.queryKey}:${id}`,
          })
        }
      } finally {
        if (!controller.signal.aborted) {
          loading.value = false
          const query = { ...route.query, ...resourceQuery }
          delete query[options.queryKey]
          void router.replace({ path: route.path, query, hash: route.hash })
        }
      }
    },
    { immediate: true },
  )

  return { loading }
}
