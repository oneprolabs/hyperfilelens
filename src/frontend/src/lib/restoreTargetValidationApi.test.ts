import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { validateRestoreTargets } from './restoreApi'

vi.mock('./api', () => ({ api: vi.fn() }))
vi.mock('../composables/useAuth', () => ({ getEffectiveOrgKey: () => 'test-org' }))

const mockedApi = vi.mocked(api)

describe('restore target validation API', () => {
  beforeEach(() => {
    mockedApi.mockReset()
  })

  it('posts snapshot and restore target bindings with cancellation support', async () => {
    const controller = new AbortController()
    const payload = {
      targets: [{
        key: 'agent:12:backup:9',
        source_snapshot_id: 41,
        target_type: 'agent' as const,
        target_ref_id: 12,
      }],
    }
    mockedApi.mockResolvedValue({
      status: 'success',
      results: [{ key: 'agent:12:backup:9', status: 'success', code: null, message: '' }],
    })

    const result = await validateRestoreTargets(payload, { signal: controller.signal })

    expect(result.status).toBe('success')
    expect(mockedApi).toHaveBeenCalledWith('/api/v1/restore/target-validations/', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: controller.signal,
      headers: { 'X-Org-Key': 'test-org' },
    })
  })
})
