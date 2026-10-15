// @vitest-environment jsdom
import { defineComponent } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { resolveSourceFailureDetails } from '../lib/sourceFailureDiagnostics'
import { openErrorDetails, type ErrorDetailsPayload } from '../lib/errors/details'
import { notifyError } from '../lib/notify'
import { useSourceFailureDetails } from './useSourceFailureDetails'

vi.mock('../lib/sourceFailureDiagnostics', () => ({ resolveSourceFailureDetails: vi.fn() }))
vi.mock('../lib/errors/details', () => ({ openErrorDetails: vi.fn() }))
vi.mock('../lib/notify', () => ({ notifyError: vi.fn() }))
vi.mock('../lib/asyncNoticeLedger', () => ({
  asyncNoticeScope: () => localStorage.getItem('hfl_org_key') || 'session',
}))
const details = { title: 'Failed', summary: 'Failed' }
function setup() {
  let diagnostics!: ReturnType<typeof useSourceFailureDetails>
  const wrapper = mount(defineComponent({
    setup() {
      diagnostics = useSourceFailureDetails(((key: string) => key) as never)
      return () => null
    },
  }))
  return { wrapper, get diagnostics() { return diagnostics } }
}
beforeEach(() => { vi.resetAllMocks(); localStorage.clear() })

describe('source failure detail activation', () => {
  it('deduplicates repeated clicks and clears loading after retrieval', async () => {
    let resolve!: (value: ErrorDetailsPayload) => void
    vi.mocked(resolveSourceFailureDetails).mockImplementation(() => new Promise(done => { resolve = done }))
    const state = setup()
    const pending = state.diagnostics.open('agent:55', 'fixture', 'upgrade_failed')
    await state.diagnostics.open('agent:55', 'fixture', 'upgrade_failed')
    expect(resolveSourceFailureDetails).toHaveBeenCalledTimes(1)
    expect(state.diagnostics.isLoading('agent:55')).toBe(true)
    resolve(details)
    await pending
    expect(openErrorDetails).toHaveBeenCalledWith(details)
    expect(state.diagnostics.isLoading('agent:55')).toBe(false)
    state.wrapper.unmount()
  })

  it.each(['unmount', 'organization'])('ignores a late response after %s', async mode => {
    let resolve!: (value: ErrorDetailsPayload) => void
    vi.mocked(resolveSourceFailureDetails).mockImplementation(() => new Promise(done => { resolve = done }))
    const state = setup()
    const pending = state.diagnostics.open('agent:55', 'fixture', 'failed')
    if (mode === 'unmount') state.wrapper.unmount()
    else localStorage.setItem('hfl_org_key', 'other-org')
    resolve(details)
    await pending
    expect(openErrorDetails).not.toHaveBeenCalled()
    if (mode !== 'unmount') state.wrapper.unmount()
  })

  it('reports request errors and allows another read attempt', async () => {
    vi.mocked(resolveSourceFailureDetails).mockRejectedValue(new Error('Network unavailable'))
    const state = setup()
    await state.diagnostics.open('agent:55', 'fixture', 'failed')
    expect(notifyError).toHaveBeenCalledWith(expect.objectContaining({ showDetails: true }))
    expect(state.diagnostics.isLoading('agent:55')).toBe(false)
    vi.mocked(resolveSourceFailureDetails).mockResolvedValue(details)
    await state.diagnostics.open('agent:55', 'fixture', 'failed')
    await flushPromises()
    expect(openErrorDetails).toHaveBeenCalledTimes(1)
    state.wrapper.unmount()
  })
})
