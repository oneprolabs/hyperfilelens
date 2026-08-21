// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import {
  deleteStorageRepository,
  preflightStorageRepositoryBinding,
  preflightStorageRepositoryCleanup,
  repairStorageRepository,
  storageRepositoryS3BucketMode,
  storageRepositoryCreateErrorMessage,
} from './storageRepositoryApi'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    api: vi.fn(),
  }
})

afterEach(() => {
  vi.clearAllMocks()
})

const translations: Record<string, string> = {
  'errors.codes.storageRepositoryAlreadyExists': 'A Kopia repository already exists at the target location.',
  'errors.codes.validationFailed': 'Check the form and try again.',
}
const t = (key: string) => translations[key] || key

describe('storageRepositoryCreateErrorMessage', () => {
  it('keeps the localized stable repository conflict message', () => {
    expect(storageRepositoryCreateErrorMessage({
      status: 409,
      message: 'Repository already exists',
      errorCode: 'STORAGE.REPOSITORY_ALREADY_EXISTS',
    }, t)).toBe('A Kopia repository already exists at the target location.')
  })

  it('uses a sanitized repository diagnostic for generic validation failures', () => {
    expect(storageRepositoryCreateErrorMessage({
      status: 400,
      message: 'Validation failed',
      errorCode: 'VALIDATION.FAILED',
      meta: { diagnostic: 'permission denied' },
    }, t)).toBe('permission denied')
  })

  it('keeps field validation errors ahead of the diagnostic', () => {
    expect(storageRepositoryCreateErrorMessage({
      status: 400,
      message: 'Validation failed',
      errorCode: 'VALIDATION.FAILED',
      fields: { name: ['Repository name is required.'] },
      meta: { diagnostic: 'internal validation detail' },
    }, t)).toBe('Repository name is required.')
  })

  it('filters generic process exit diagnostics', () => {
    expect(storageRepositoryCreateErrorMessage({
      status: 400,
      message: 'Validation failed',
      errorCode: 'VALIDATION.FAILED',
      meta: { diagnostic: 'exit 1: exit status 1' },
    }, t)).toBe('Check the form and try again.')
  })
})

describe('storageRepositoryS3BucketMode', () => {
  it.each([
    ['existing', 'existing'],
    ['new', 'new'],
  ] as const)('maps the %s UI selection to the API value', (selection, expected) => {
    expect(storageRepositoryS3BucketMode(selection)).toBe(expected)
  })
})

describe('repository cleanup requests', () => {
  it('sends a normal repository deletion through DELETE', async () => {
    vi.mocked(api).mockResolvedValue({ uuid: 'cleanup-task' })

    await deleteStorageRepository(17)

    expect(api).toHaveBeenCalledWith('/api/v1/storage/repositories/17/', {
      method: 'DELETE',
      body: JSON.stringify({ force: false, confirmation: '' }),
      headers: { 'X-Org-Key': '' },
    })
  })

  it('sends the required confirmation with a force deletion', async () => {
    vi.mocked(api).mockResolvedValue({ uuid: 'force-cleanup-task' })

    await deleteStorageRepository(23, true)

    expect(api).toHaveBeenCalledWith('/api/v1/storage/repositories/23/', {
      method: 'DELETE',
      body: JSON.stringify({ force: true, confirmation: 'FORCE CLEANUP' }),
      headers: { 'X-Org-Key': '' },
    })
  })

  it('passes force mode to repository cleanup preflight', async () => {
    vi.mocked(api).mockResolvedValue({ allowed: true, warnings: [] })

    await preflightStorageRepositoryCleanup(31, true)

    expect(api).toHaveBeenCalledWith('/api/v1/storage/repositories/31/cleanup/preflight/', {
      method: 'POST',
      body: JSON.stringify({ force: true }),
      headers: { 'X-Org-Key': '' },
    })
  })
})

describe('NAS binding recovery requests', () => {
  it('preflights the selected Proxy before binding', async () => {
    vi.mocked(api).mockResolvedValue({
      allowed: false,
      recovery_eligible: true,
      required_action: 'cleanup_and_bind',
    })

    await preflightStorageRepositoryBinding(31, 24)

    expect(api).toHaveBeenCalledWith(
      '/api/v1/storage/repositories/31/repair/binding-preflight/',
      {
        method: 'POST',
        body: JSON.stringify({ bind_node_id: 24 }),
        headers: { 'X-Org-Key': '' },
      },
    )
  })

  it('sends explicit cleanup confirmation only for the recovery request', async () => {
    vi.mocked(api).mockResolvedValue({ id: 31, status: 'creating' })

    await repairStorageRepository(31, {
      bind_node_id: 24,
      cleanup_failed_provisioning_targets: true,
      cleanup_confirmation: 'CLEAN UP AND BIND',
    })

    expect(api).toHaveBeenCalledWith('/api/v1/storage/repositories/31/repair/', {
      method: 'PATCH',
      body: JSON.stringify({
        bind_node_id: 24,
        cleanup_failed_provisioning_targets: true,
        cleanup_confirmation: 'CLEAN UP AND BIND',
      }),
      headers: { 'X-Org-Key': '' },
    })
  })
})
