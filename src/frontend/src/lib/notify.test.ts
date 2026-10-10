// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { closeErrorDetails, errorDetailsState } from './errors/details'
import { notifyError } from './notify'
import { resetToastStoreForTests, toastState } from './toast/store'

describe('notify error presentation', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetToastStoreForTests()
    closeErrorDetails()
  })

  afterEach(() => {
    resetToastStoreForTests()
    closeErrorDetails()
    vi.useRealTimers()
  })

  const error = {
    status: 503,
    message: 'Endpoint unavailable',
    errorCode: 'ENDPOINT.UNAVAILABLE',
    traceId: 'trace-001',
    detail: { endpoint: 'https://example.invalid' },
  }

  it('keeps supplemental details in a longer-lived toast', () => {
    notifyError({
      title: 'Save failed',
      message: 'Endpoint unavailable',
      error,
      showDetails: true,
    })

    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0]?.duration).toBe(12000)
    expect(toastState.items[0]?.details?.errorCode).toBe('ENDPOINT.UNAVAILABLE')
    expect(errorDetailsState.current).toBeNull()
  })

  it('opens blocking details directly without creating a toast', () => {
    notifyError({
      title: 'Connection failed',
      message: 'Endpoint unavailable',
      error,
      showDetails: true,
      detailsPresentation: 'dialog',
    })

    expect(toastState.items).toHaveLength(0)
    expect(errorDetailsState.current?.errorCode).toBe('ENDPOINT.UNAVAILABLE')
  })

  it('avoids repeating the same title and message in a toast', () => {
    notifyError({
      title: 'Delete failed',
      message: 'Delete failed',
      error,
      showDetails: true,
    })
    expect(toastState.items[0]?.title).toBeUndefined()
  })

  it('accepts a prebuilt details payload without requiring error', () => {
    notifyError({
      title: 'Source deregistration failed',
      message: 'Deregistration failed for linux-32.',
      showDetails: true,
      details: {
        title: 'Source deregistration failed',
        summary: 'Deregistration failed for linux-32.',
        reasons: ['Repository cleanup failed'],
        resolutions: ['Retry Strict Cleanup'],
        rawDetail: { task_uuid: 'task-1' },
      },
    })

    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0]?.details?.reasons).toEqual(['Repository cleanup failed'])
    expect(toastState.items[0]?.details?.rawDetail).toEqual({ task_uuid: 'task-1' })
  })
})

describe('persisted async notice identity', () => {
  it('deduplicates a backup result after closing its toast, but not its retry', () => {
    sessionStorage.clear()
    resetToastStoreForTests()
    const details = { title: 'Backup failed', summary: 'Backup failed', taskUuid: 'backup-ledger', taskType: 'backup', taskAttempt: 0 }
    notifyError({ message: details.summary, showDetails: true, details })
    resetToastStoreForTests()
    notifyError({ message: details.summary, showDetails: true, details })
    expect(toastState.items).toHaveLength(0)
    notifyError({ message: details.summary, showDetails: true, details: { ...details, taskAttempt: 1 } })
    expect(toastState.items).toHaveLength(1)
    resetToastStoreForTests()
  })
})
