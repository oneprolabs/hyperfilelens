import { afterEach, describe, expect, it } from 'vitest'
import {
  closeErrorDetails,
  errorDetailsCopyText,
  errorDetailsState,
  openErrorDetails,
  safeErrorDetailText,
  toErrorDetails,
} from './details'

describe('error details', () => {
  afterEach(closeErrorDetails)

  it('keeps task view context separate from diagnostic data and resets it on reopen', () => {
    const payload = { title: 'Failed', summary: 'Read failed', taskUuid: 'task-1' }
    openErrorDetails(payload, { currentTaskUuid: 'task-1' })
    expect(errorDetailsState.currentTaskUuid).toBe('task-1')
    expect(errorDetailsState.current).not.toHaveProperty('currentTaskUuid')
    openErrorDetails(payload)
    expect(errorDetailsState.currentTaskUuid).toBeUndefined()
    openErrorDetails(payload, { currentTaskUuid: 'task-1' })
    closeErrorDetails()
    expect(errorDetailsState.currentTaskUuid).toBeUndefined()
  })

  it('preserves async overrides and redacts the stored and copied bundle', () => {
    const payload = toErrorDetails(new Error('Failed'), {
      title: 'Warning', summary: 'Some files were skipped', severity: 'warning',
      taskUuid: 'task-1', taskType: 'backup', failedStep: 'snapshot',
      entities: [{ id: 'source-1', name: 'Source', type: 'source', error: 'password=secret-value' }],
      cleanupResidue: { hasResidue: true, retainedResources: ['token=secret-token'] },
      rawDetail: { password: 'raw-secret' },
    })
    openErrorDetails(payload)
    expect(errorDetailsState.current?.severity).toBe('warning')
    expect(errorDetailsState.current?.taskUuid).toBe('task-1')
    expect(JSON.stringify(errorDetailsState.current)).not.toMatch(/secret-value|secret-token|raw-secret/)
    const copied = errorDetailsCopyText(payload)
    expect(copied).toContain('Some files were skipped')
    expect(copied).toContain('source-1')
    expect(copied).toContain('task-1')
    expect(copied).not.toMatch(/secret-value|secret-token|raw-secret/)
  })

  it('maps structured application errors', () => {
    const details = toErrorDetails({
      status: 504,
      message: 'Connection timed out',
      errorCode: 'STORAGE.TIMEOUT',
      traceId: 'trace-001',
      detail: { endpoint: 'https://example.invalid' },
    })

    expect(details.errorCode).toBe('STORAGE.TIMEOUT')
    expect(details.traceId).toBe('trace-001')
    expect(details.issue).toBe('Connection timed out')
  })

  it('redacts secrets from objects and raw strings', () => {
    const text = safeErrorDetailText({
      endpoint: 'https://example.invalid',
      secret_access_key: 'super-secret',
      nested: { authorization: 'Bearer abc.def.ghi' },
      raw: 'password=hunter2 token: abc123',
      endpointWithQuery: 'https://storage.example.test/upload?access_key=query-secret&retry=1',
    })

    expect(text).toContain('https://example.invalid')
    expect(text).not.toContain('super-secret')
    expect(text).not.toContain('abc.def.ghi')
    expect(text).not.toContain('hunter2')
    expect(text).not.toContain('abc123')
    expect(text).not.toContain('query-secret')
    expect(text).toContain('[REDACTED]')
  })

  it('redacts secrets embedded in the visible issue and copied text', () => {
    const details = toErrorDetails(new Error('password=hunter2 request failed'))
    expect(details.issue).not.toContain('hunter2')
    expect(errorDetailsCopyText(details)).not.toContain('hunter2')
  })

  it('builds copyable diagnostic text and controls the global dialog state', () => {
    const payload = {
      title: 'Connection failed',
      summary: 'Unable to connect',
      errorCode: 'NETWORK.TIMEOUT',
      traceId: 'trace-002',
      issue: 'The endpoint timed out.',
      reasons: ['The endpoint is offline.'],
      resolutions: ['Check the endpoint.'],
    }
    openErrorDetails(payload)
    expect(errorDetailsState.current?.errorCode).toBe('NETWORK.TIMEOUT')
    expect(errorDetailsCopyText(payload)).toContain('How to resolve')
    closeErrorDetails()
    expect(errorDetailsState.current).toBeNull()
  })
})
