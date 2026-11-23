import { onScopeDispose, reactive, ref, watch, type Ref } from 'vue'
import { toApiError } from '../../../lib/errors/normalizer'
import { listRestoreRecords, type RestoreEndpointType, type RestoreRecord } from '../../../lib/restoreApi'

type RestoreDrawerSource = {
  source_type: RestoreEndpointType
  source_ref_id: number
  backup_config_ids: string
}

export function useRestoreTaskDrawerPage(
  source: Readonly<Ref<RestoreDrawerSource | null>>,
  group: 'running' | 'history',
  initialPageSize: number,
) {
  const pager = reactive({ page: 1, pageSize: initialPageSize })
  const records = ref<RestoreRecord[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref(false)
  let controller: AbortController | undefined

  function reset() {
    controller?.abort()
    controller = undefined
    records.value = []
    total.value = 0
    loading.value = false
    error.value = false
    pager.page = 1
  }

  async function load(background = false) {
    const context = source.value
    if (!context || (background && controller)) return
    controller?.abort()
    const request = new AbortController()
    controller = request
    const { page, pageSize } = pager
    const isCurrent = () => !request.signal.aborted && source.value === context
      && pager.page === page && pager.pageSize === pageSize
    if (!background) {
      records.value = []
      error.value = false
      loading.value = true
    }
    try {
      const result = await listRestoreRecords({
        ...context,
        page,
        page_size: pageSize,
        ...(group === 'running' ? { running_only: true } : { history_only: true }),
      }, { signal: request.signal })
      if (!isCurrent()) return
      records.value = result.results
      total.value = result.count
      error.value = false
    } catch (failure) {
      if (!isCurrent()) return
      if (page > 1 && toApiError(failure).status === 404) pager.page = 1
      else if (!background) error.value = true
    } finally {
      if (controller === request) {
        controller = undefined
        loading.value = false
      }
    }
  }

  function changePageSize(pageSize: number) {
    pager.pageSize = pageSize
    pager.page = 1
  }

  watch(source, reset, { flush: 'sync' })
  watch([source, () => pager.page, () => pager.pageSize], () => { void load() }, { immediate: true })
  onScopeDispose(() => controller?.abort())

  return { pager, records, total, loading, error, load, changePageSize }
}
