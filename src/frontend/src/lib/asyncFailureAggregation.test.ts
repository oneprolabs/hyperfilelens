// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mergeUnregisterDetails, notifyUnregisterOperation } from './unregisterFailureDetails'
import { resetToastStoreForTests, toastState } from './toast/store'
import type { ComposerTranslation } from 'vue-i18n'
vi.mock('./taskApi', () => ({ retryTask: vi.fn() }))
vi.mock('./api', () => ({ apiErrorMessageI18n: vi.fn() }))
const t = ((key: string) => key) as ComposerTranslation
const item = (uuid: string, attempt = 0) => ({ sourceId: uuid, details: {
  title: 'Failed', summary: 'Failed', taskUuid: uuid, taskType: 'source_unregister', taskAttempt: attempt,
  severity: 'error' as const, reasons: ['Unchanged reason'], resolutions: ['Unchanged remedy'],
  entities: [{ id: uuid, name: uuid, type: 'source' as const, error: 'Unchanged reason' }],
} })
beforeEach(() => { sessionStorage.clear(); resetToastStoreForTests() })
describe('operation-scoped failure aggregation', () => {
  it('updates one notice as targets finish in different polls, retaining every target', () => {
    notifyUnregisterOperation({ t, operationId: 'staggered', items: [item('a')] })
    notifyUnregisterOperation({ t, operationId: 'staggered', items: [item('b')] })
    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0].details?.entities?.map(entity => entity.id)).toEqual(['a', 'b'])
    expect(toastState.items[0].details?.relatedTasks).toHaveLength(2)
    expect(toastState.items[0].details?.resolutions).toEqual(['Unchanged remedy'])
  })
  it('does not re-notify after dismiss or refresh but allows a new retry attempt', () => {
    notifyUnregisterOperation({ t, operationId: 'retry', items: [item('retry-task')] })
    resetToastStoreForTests()
    notifyUnregisterOperation({ t, operationId: 'retry', items: [item('retry-task')] })
    expect(toastState.items).toHaveLength(0)
    notifyUnregisterOperation({ t, operationId: 'retry', items: [item('retry-task', 1)] })
    expect(toastState.items).toHaveLength(1)
  })
  it('uses warning severity for mixed success and failure without rewriting reasons', () => {
    notifyUnregisterOperation({ t, operationId: 'mixed', items: [item('mixed-failure')], partialSuccess: true })
    expect(toastState.items[0].type).toBe('warning')
    expect(toastState.items[0].details?.reasons).toEqual(['Unchanged reason'])
  })
  it('retains cleanup failures and skipped items in merged details', () => {
    const a = { ...item('cleanup-a').details, cleanupResidue: { hasResidue: true, failures: ['busy'], skippedItems: ['file-a'] } }
    const b = { ...item('cleanup-b').details, cleanupResidue: { hasResidue: true, retainedResources: ['mount-b'] } }
    const result = mergeUnregisterDetails(t, [a, b], 'cleanup_warning')
    expect(result.cleanupResidue).toMatchObject({ failures: ['busy'], skippedItems: ['file-a'], retainedResources: ['mount-b'] })
    expect(result.relatedTasks?.map(task => task.taskUuid)).toEqual(['cleanup-a', 'cleanup-b'])
  })
  it('remembers a clean success observed before a later failure', () => {
    notifyUnregisterOperation({ t, operationId: 'early-success', items: [], partialSuccess: true })
    notifyUnregisterOperation({ t, operationId: 'early-success', items: [item('late-failure')] })
    expect(toastState.items[0].type).toBe('warning')
  })

})
