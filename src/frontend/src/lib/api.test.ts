// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, apiErrorMessage, apiErrorMessageI18n } from './api'
import { getRouteRequestSignal } from './routeRequestAbort'

vi.mock('./routeRequestAbort', () => ({
  getRouteRequestSignal: vi.fn(),
}))

const routeSignal = new AbortController().signal

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('api request cancellation', () => {
  it('keeps a caller-provided signal independent from route query changes', async () => {
    const requestController = new AbortController()
    vi.mocked(getRouteRequestSignal).mockReturnValue(routeSignal)
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await api('/api/v1/source/backup-selectable/', { signal: requestController.signal })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(requestController.signal)
  })

  it('uses the route signal when the caller does not provide one', async () => {
    vi.mocked(getRouteRequestSignal).mockReturnValue(routeSignal)
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await api('/api/v1/source/backup-selectable/')

    expect(fetchMock.mock.calls[0][1]?.signal).toBe(routeSignal)
  })

  it('lets the browser set the multipart boundary for FormData requests', async () => {
    vi.mocked(getRouteRequestSignal).mockReturnValue(routeSignal)
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const body = new FormData()
    body.append('file', new File(['content'], 'report.pdf', { type: 'application/pdf' }))

    await api('/api/v1/lens/copilot/sessions/1/attachments/', {
      method: 'POST',
      body,
    })

    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers['Content-Type']).toBeUndefined()
  })
})

describe('api validation errors', () => {
  it('shows the backend field message instead of the generic validation message', async () => {
    vi.mocked(getRouteRequestSignal).mockReturnValue(routeSignal)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 400,
      message: 'failed',
      data: {
        title: 'Validation failed',
        status: 400,
        code: 'VALIDATION.FAILED',
        errors: [{
          field: 'model',
          code: 'VALIDATION.FIELD_INVALID',
          message: 'Configure an active AI model before creating a chat.',
        }],
      },
    }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })))

    let message = ''
    try {
      await api('/api/v1/lens/copilot/sessions/', { method: 'POST' })
    } catch (error) {
      message = apiErrorMessage(error)
    }

    expect(message).toBe('Configure an active AI model before creating a chat.')
  })
})

describe('repository conflict errors', () => {
  it('maps an existing repository conflict to localized copy', () => {
    const message = apiErrorMessageI18n(
      {
        status: 409,
        message: 'Repository already exists',
        errorCode: 'STORAGE.REPOSITORY_ALREADY_EXISTS',
      },
      (key) => key === 'errors.codes.storageRepositoryAlreadyExists'
        ? 'A Kopia repository already exists at the selected location. Import is not supported in this version. Choose a different storage location.'
        : key,
    )

    expect(message).toBe('A Kopia repository already exists at the selected location. Import is not supported in this version. Choose a different storage location.')
  })

  it.each([
    [
      'STORAGE.REPOSITORY_OPERATION_NOT_CANCELLABLE',
      'errors.codes.storageRepositoryOperationNotCancellable',
      'Only controller-managed S3 maintenance tasks can be cancelled.',
    ],
    [
      'STORAGE.REPOSITORY_OPERATION_NOT_ACTIVE',
      'errors.codes.storageRepositoryOperationNotActive',
      'This maintenance task has already finished. Refresh to see its latest status.',
    ],
  ])('maps %s to stable cancellation copy', (errorCode, key, copy) => {
    const message = apiErrorMessageI18n(
      { status: 409, message: 'internal diagnostic', errorCode },
      (candidate) => candidate === key ? copy : candidate,
    )

    expect(message).toBe(copy)
  })
})

describe('object storage validation errors', () => {
  it.each([
    ['STORAGE.S3_CREDENTIALS_INVALID', 'errors.codes.storageS3CredentialsInvalid'],
    ['STORAGE.S3_PERMISSION_DENIED', 'errors.codes.storageS3PermissionDenied'],
    ['STORAGE.S3_BUCKET_ACCESS_DENIED', 'errors.codes.storageS3BucketAccessDenied'],
    ['STORAGE.S3_BUCKET_NOT_FOUND', 'errors.codes.storageS3BucketNotFound'],
    ['STORAGE.S3_BUCKET_NAME_INVALID', 'errors.codes.storageS3BucketNameInvalid'],
    ['STORAGE.S3_BUCKET_NAME_UNAVAILABLE', 'errors.codes.storageS3BucketNameUnavailable'],
    ['STORAGE.S3_CONFIGURATION_INVALID', 'errors.codes.storageS3ConfigurationInvalid'],
    ['STORAGE.S3_NETWORK_UNAVAILABLE', 'errors.codes.storageS3NetworkUnavailable'],
    ['STORAGE.S3_TIMEOUT', 'errors.codes.storageS3Timeout'],
    ['STORAGE.S3_TLS_FAILED', 'errors.codes.storageS3TlsFailed'],
    ['STORAGE.S3_VALIDATION_FAILED', 'errors.codes.storageS3ValidationFailed'],
    ['AGENT.PATH_PERMISSION_DENIED', 'errors.codes.agentPathPermissionDenied'],
  ])('maps %s without exposing the backend diagnostic', (errorCode, expectedKey) => {
    const message = apiErrorMessageI18n(
      { status: 400, message: 'secret upstream diagnostic', errorCode },
      (key) => key,
    )

    expect(message).toBe(expectedKey)
    expect(message).not.toContain('secret upstream diagnostic')
  })
})
