// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { listBackupSelectableSources, productionSourceSummary, testSourceDraft } from './sourceApi'


afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('listBackupSelectableSources', () => {
  it('serializes the Pipeline-backed search and advanced filter contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      page: 2,
      page_size: 25,
      count: 0,
      results: [],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await listBackupSelectableSources({
      page: 2,
      page_size: 25,
      step: 3,
      search: 'proxy-01',
      search_field: 'source_hostname',
      source_status: 'online',
      availability: 'online',
      running_task: 'restore',
      backup_running: false,
      backup_policy_id: 11,
      file_filter_rule_id: 12,
      repository_id: 13,
    })

    const url = new URL(String(fetchMock.mock.calls[0][0]), window.location.origin)
    expect(url.pathname).toBe('/api/v1/source/backup-selectable/')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      page: '2',
      page_size: '25',
      step: '3',
      search: 'proxy-01',
      search_field: 'source_hostname',
      source_status: 'online',
      availability: 'online',
      running_task: 'restore',
      backup_running: 'false',
      backup_policy_id: '11',
      file_filter_rule_id: '12',
      repository_id: '13',
    })
  })

  it('omits cleared optional filters and preserves a caller signal', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ count: 0, results: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await listBackupSelectableSources({
      page: 1,
      search: '',
      source_name: undefined,
      repository_id: undefined,
    }, { signal: controller.signal })

    const url = new URL(String(fetchMock.mock.calls[0][0]), window.location.origin)
    expect(Object.fromEntries(url.searchParams)).toEqual({ page: '1' })
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal)
  })
})

describe('productionSourceSummary', () => {
  it('loads the canonical Agent and NAS summary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      total: 4,
      available: 3,
      unavailable: 1,
      hosts: { total: 2, available: 1, unavailable: 1 },
      nas: { total: 2, available: 2, unavailable: 0 },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(productionSourceSummary()).resolves.toMatchObject({
      total: 4,
      available: 3,
      unavailable: 1,
    })

    const url = new URL(String(fetchMock.mock.calls[0][0]), window.location.origin)
    expect(url.pathname).toBe('/api/v1/source/resources/production-summary/')
  })
})

describe('testSourceDraft', () => {
  it('preserves a structured charset failure from a 400 response envelope', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 400,
      message: 'mount error(79)',
      data: {
        success: false,
        message: 'mount error(79)',
        error_code: 'SMB_CHARSET_UNAVAILABLE',
        details: {
          storage_type: 'nas',
          protocol: 'smb',
          charset: 'utf8',
          cleanup_status: 'success',
        },
      },
    }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })))

    const result = await testSourceDraft({
      resource_type: 'nas',
      bound_node_id: 13,
      config: { protocol: 'smb', options: 'rw,iocharset=utf8' },
    })

    expect(result).toEqual({
      success: false,
      message: 'mount error(79)',
      error_code: 'SMB_CHARSET_UNAVAILABLE',
      details: {
        storage_type: 'nas',
        protocol: 'smb',
        charset: 'utf8',
        cleanup_status: 'success',
      },
    })
  })
})
