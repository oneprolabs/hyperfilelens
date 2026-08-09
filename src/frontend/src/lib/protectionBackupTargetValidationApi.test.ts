import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { validateProtectionBackupTargets } from './protectionBackupTargetValidationApi'

vi.mock('./api', () => ({ api: vi.fn() }))

const mockedApi = vi.mocked(api)

describe('protectionBackupTargetValidationApi', () => {
  beforeEach(() => {
    mockedApi.mockReset()
  })

  it('posts the source assignment snapshot and forwards the abort signal', async () => {
    const controller = new AbortController()
    mockedApi.mockResolvedValue({
      status: 'success',
      results: [{ key: 'agent:12', status: 'success', code: null, message: '' }],
    })
    const payload = {
      sources: [{
        key: 'agent:12',
        source_type: 'agent' as const,
        source_ref_id: 12,
        repository_id: 34,
        repository_endpoint_type: 'external' as const,
      }],
    }

    const result = await validateProtectionBackupTargets(payload, { signal: controller.signal })

    expect(result.status).toBe('success')
    expect(mockedApi).toHaveBeenCalledWith(
      '/api/v1/protection/backup-target-validations/',
      {
        method: 'POST',
        body: JSON.stringify(payload),
        signal: controller.signal,
      },
    )
  })

  it('unwraps standard API envelopes', async () => {
    mockedApi.mockResolvedValue({
      code: 0,
      message: 'ok',
      data: {
        status: 'failed',
        results: [{
          key: 'agent:12',
          status: 'failed',
          code: 'PROXY_REPOSITORY_SERVER_UNREACHABLE',
          message: 'dial tcp: i/o timeout',
          details: {
            stage: 'source_probe',
            proxy_address: '192.168.10.33',
            port_range: '51515-52014',
          },
        }],
      },
    })

    const result = await validateProtectionBackupTargets({
      sources: [{
        key: 'agent:12',
        source_type: 'agent',
        source_ref_id: 12,
        repository_id: 34,
        repository_endpoint_type: 'external',
      }],
    })

    expect(result.results[0]).toMatchObject({
      code: 'PROXY_REPOSITORY_SERVER_UNREACHABLE',
      details: {
        stage: 'source_probe',
        proxy_address: '192.168.10.33',
        port_range: '51515-52014',
      },
    })
  })
})
