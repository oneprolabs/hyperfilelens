/** Budgets for interactive, read-only path operations; not Agent watchdogs. */
export const SOURCE_PATH_WAIT_SECONDS = 30
export const SOURCE_PATH_CLIENT_TIMEOUT_MS = 40_000

export function createSourcePathRequests() {
  const active = new Map<string, AbortController>()

  function cancel(key: string) {
    const controller = active.get(key)
    active.delete(key)
    controller?.abort()
  }

  return {
    cancel,
    cancelPrefix(prefix: string) {
      for (const key of active.keys()) {
        if (key.startsWith(prefix)) cancel(key)
      }
    },
    cancelAll() {
      for (const key of active.keys()) cancel(key)
    },
    start(key: string, contextIsCurrent: () => boolean = () => true) {
      cancel(key)
      const controller = new AbortController()
      active.set(key, controller)
      return {
        // Next-tick UI work must still check context after finish releases ownership.
        contextIsCurrent,
        isCurrent: () => active.get(key) === controller && contextIsCurrent(),
        finish() {
          if (active.get(key) !== controller) return false
          active.delete(key)
          return true
        },
        async run<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
          let timer: ReturnType<typeof setTimeout> | undefined
          let onAbort: () => void = () => {}
          const interrupted = new Promise<never>((_resolve, reject) => {
            onAbort = () => reject(controller.signal.reason)
            controller.signal.addEventListener('abort', onAbort, { once: true })
            timer = setTimeout(() => {
              // Stop waiting locally; do not cancel or redispatch the Agent task.
              controller.abort(Object.assign(new Error('Source path request timed out.'), {
                status: 504,
                errorCode: 'AGENT.TIMEOUT',
              }))
            }, SOURCE_PATH_CLIENT_TIMEOUT_MS)
          })
          try {
            if (controller.signal.aborted) throw controller.signal.reason
            return await Promise.race([operation(controller.signal), interrupted])
          } finally {
            clearTimeout(timer)
            controller.signal.removeEventListener('abort', onAbort)
          }
        },
      }
    },
  }
}
