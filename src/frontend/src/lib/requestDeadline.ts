export function withRequestDeadline<Result>(
  run: (signal: AbortSignal) => Promise<Result>,
  parent?: AbortSignal | null,
  timeoutMs = 15_000,
): Promise<Result> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (parent?.aborted) return Promise.reject(new DOMException('Request cancelled', 'AbortError'))
  parent?.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(() => controller.abort(new DOMException('Request timed out', 'TimeoutError')), timeoutMs)
  const cancelled = new Promise<never>((_resolve, reject) => {
    controller.signal.addEventListener('abort', () => reject(controller.signal.reason), { once: true })
  })
  return Promise.race([cancelled, Promise.resolve().then(() => {
    controller.signal.throwIfAborted()
    return run(controller.signal)
  })]).finally(() => {
    clearTimeout(timer)
    parent?.removeEventListener('abort', abort)
    controller.abort()
  })
}
