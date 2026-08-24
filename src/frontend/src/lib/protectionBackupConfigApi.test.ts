// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createBackupConfig,
  InvalidCompressionLevelError,
  parseCompressionLevel,
} from './protectionBackupConfigApi'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('parseCompressionLevel', () => {
  it.each(['none', 'balanced', 'high'] as const)('accepts %s', (value) => {
    expect(parseCompressionLevel(value)).toBe(value)
  })

  it.each(['', 'best', 'zstd', null, undefined])('rejects unexpected value %s', (value) => {
    expect(() => parseCompressionLevel(value)).toThrow(InvalidCompressionLevelError)
  })
})

describe('createBackupConfig', () => {
  it('sends the caller idempotency key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 32,
      name: 'Config',
      remark: '',
      source_type: 'agent',
      source_ref_id: 12,
      repository_id: 7,
      repository_endpoint_type: 'external',
      backup_policy_id: null,
      file_filter_rule_id: null,
      directory_count: 1,
      compression_level: 'balanced',
      status: 'active',
      recovery_plan_enabled: false,
      directories: [],
      recovery_plans: [],
      created_at: '2026-08-24T00:00:00Z',
      updated_at: '2026-08-24T00:00:00Z',
    }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await createBackupConfig({
      name: 'Config',
      source_type: 'agent',
      source_ref_id: 12,
      repository_id: 7,
      directories: [{ path: '/data' }],
    }, 'backup-config:agent:12:test-key')

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers)
    expect(headers.get('Idempotency-Key')).toBe('backup-config:agent:12:test-key')
  })
})
