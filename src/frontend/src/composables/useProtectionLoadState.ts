import { computed, shallowRef } from 'vue'
import { isAbortError, toApiError } from '../lib/errors/normalizer'

/** A failed protected read is not an empty list, nor a reason to keep polling. */
export function useProtectionLoadState() {
  const error = shallowRef<unknown>(null)
  const denied = computed(() => {
    const status = toApiError(error.value).status
    return status === 401 || status === 403
  })

  function fail(reason: unknown) {
    if (!isAbortError(reason)) error.value = reason
  }

  async function initialize(load: () => Promise<void>, settle: (failed: boolean) => void) {
    error.value = null
    let failed = false
    try {
      await load()
    } catch (reason) {
      failed = true
      fail(reason)
    } finally {
      // Bootstrap can fail before any table request has had a chance to run.
      settle(failed || error.value !== null)
    }
  }

  return { error, denied, fail, initialize }
}
