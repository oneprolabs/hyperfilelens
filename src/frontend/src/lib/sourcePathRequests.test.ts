import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSourcePathRequests, SOURCE_PATH_CLIENT_TIMEOUT_MS, SOURCE_PATH_WAIT_SECONDS } from './sourcePathRequests'

afterEach(() => vi.useRealTimers())

describe('interactive source path requests', () => {
  it('accepts a result after eleven seconds without redispatching', async () => {
    vi.useFakeTimers()
    const requests = createSourcePathRequests()
    const request = requests.start('agent:1|manual')
    const operation = vi.fn(() => new Promise<string>((resolve) => setTimeout(() => resolve('directory'), 11_200)))
    const result = request.run(operation)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(request.isCurrent()).toBe(true)
    await vi.advanceTimersByTimeAsync(1_200)
    await expect(result).resolves.toBe('directory')
    expect(operation).toHaveBeenCalledOnce()
    expect(SOURCE_PATH_WAIT_SECONDS).toBe(30)
    expect(vi.getTimerCount()).toBe(0)
    expect(request.finish()).toBe(true)
  })

  it('bounds an unresponsive HTTP request and aborts only the browser operation', async () => {
    vi.useFakeTimers()
    const request = createSourcePathRequests().start('agent:1|manual')
    let signal!: AbortSignal
    const operation = vi.fn((value: AbortSignal) => {
      signal = value
      return new Promise<never>(() => {})
    })
    const outcome = expect(request.run(operation)).rejects.toMatchObject({
      errorCode: 'AGENT.TIMEOUT', status: 504,
    })
    await vi.advanceTimersByTimeAsync(SOURCE_PATH_CLIENT_TIMEOUT_MS)
    await outcome
    expect(signal.aborted).toBe(true)
    expect(operation).toHaveBeenCalledOnce()
    // Timeout is an actionable error; cancellation/supersession is silent.
    expect(request.isCurrent()).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not let an older completion clear the replacement request', async () => {
    const requests = createSourcePathRequests()
    const old = requests.start('agent:1|manual')
    const outcome = expect(old.run(() => new Promise<never>(() => {}))).rejects.toMatchObject({ name: 'AbortError' })
    const current = requests.start('agent:1|manual')
    await outcome
    expect(old.isCurrent()).toBe(false)
    expect(old.finish()).toBe(false)
    expect(current.isCurrent()).toBe(true)
    await expect(current.run(async () => 'new path')).resolves.toBe('new path')
    expect(current.finish()).toBe(true)
  })

  it('cancels one tree without canceling other sources or manual validation', async () => {
    const requests = createSourcePathRequests()
    const tree = requests.start('agent:1|directory|root')
    const manual = requests.start('agent:1|manual')
    const other = requests.start('agent:2|directory|root')
    const outcome = expect(tree.run(() => new Promise<never>(() => {}))).rejects.toMatchObject({ name: 'AbortError' })
    requests.cancelPrefix('agent:1|directory|')
    await outcome
    expect(tree.isCurrent()).toBe(false)
    expect(manual.isCurrent()).toBe(true)
    expect(other.isCurrent()).toBe(true)
    requests.cancelAll()
    expect(manual.isCurrent()).toBe(false)
    expect(other.isCurrent()).toBe(false)
  })

  it('rejects stale source, organization, or tree context even without an abort', () => {
    let revision = 1
    const request = createSourcePathRequests().start('agent:1|manual', () => revision === 1)
    expect(request.isCurrent()).toBe(true)
    revision = 2
    expect(request.isCurrent()).toBe(false)
    expect(request.contextIsCurrent()).toBe(false)
    request.finish()
  })

  it('preserves explicit permission errors and cleans up its deadline', async () => {
    vi.useFakeTimers()
    const request = createSourcePathRequests().start('nas:1|manual')
    const error = { errorCode: 'AGENT.PATH_PERMISSION_DENIED', status: 403 }
    await expect(request.run(async () => { throw error })).rejects.toBe(error)
    expect(vi.getTimerCount()).toBe(0)
    request.finish()
  })
})
