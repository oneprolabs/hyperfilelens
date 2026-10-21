import { describe, expect, it, vi } from 'vitest'
import { useProtectionLoadState } from './useProtectionLoadState'

describe('protected page bootstrap failure', () => {
  it.each([401, 403])('settles primed loading when bootstrap fails with %s before the table request', async status => {
    const state = useProtectionLoadState()
    const tableRequest = vi.fn()
    const settle = vi.fn()
    await state.initialize(async () => {
      await Promise.reject({ status, code: 'AUTH.FORBIDDEN' })
      tableRequest()
    }, settle)
    expect(tableRequest).not.toHaveBeenCalled()
    expect(settle).toHaveBeenCalledExactlyOnceWith(true)
    expect(state.denied.value).toBe(true)
    expect(state.error.value).toEqual({ status, code: 'AUTH.FORBIDDEN' })
  })

  it('allows explicit recovery after a transient bootstrap failure', async () => {
    const state = useProtectionLoadState()
    const settle = vi.fn()
    await state.initialize(() => Promise.reject({ status: 503 }), settle)
    expect(state.error.value).toEqual({ status: 503 })
    expect(state.denied.value).toBe(false)
    await state.initialize(() => Promise.resolve(), settle)
    expect(state.error.value).toBeNull()
    expect(settle).toHaveBeenCalledTimes(2)
    expect(settle.mock.calls).toEqual([[true], [false]])
  })

  it('settles aborted initialization without showing it as a load failure', async () => {
    const state = useProtectionLoadState()
    const settle = vi.fn()
    await state.initialize(() => Promise.reject(new DOMException('Aborted', 'AbortError')), settle)
    expect(settle).toHaveBeenCalledOnce()
    expect(settle).toHaveBeenCalledWith(true)
    expect(state.error.value).toBeNull()
    expect(state.denied.value).toBe(false)
  })

  it('reports an internally handled failure so pending sibling requests can be cancelled', async () => {
    const state = useProtectionLoadState()
    const settle = vi.fn()
    await state.initialize(async () => {
      state.fail({ status: 503, message: 'List unavailable' })
    }, settle)
    expect(settle).toHaveBeenCalledExactlyOnceWith(true)
  })
})
