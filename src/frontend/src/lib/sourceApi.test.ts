// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  bulkDeleteBackupSources,
  captureBackupSourceFiles,
  listBackupSelectableSources,
  productionSourceSummary,
  testSourceDraft,
} from './sourceApi'


afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('captureBackupSourceFiles', () => {
  it('posts a bounded point-in-time capture request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      capture_id: 'capture-1',
      source_id: 'agent:2',
      root_path: '/data',
      scope_mode: 'static_recursive_files',
      captured_at: '2026-08-24T09:00:00Z',
      manifest_hash: 'hash',
      entry_count: 0,
      file_count: 0,
      directory_count: 0,
      entries: [],
      files: [],
      task_id: 'task-1',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await captureBackupSourceFiles({
      source_id: 'agent:2',
      path: '/data',
      mode: 'recursive',
      timeout: 120,
      max_files: 10000,
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(new URL(String(url), window.location.origin).pathname)
      .toBe('/api/v1/source/backup-selectable/file-capture/')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      source_id: 'agent:2',
      path: '/data',
      mode: 'recursive',
      max_files: 10000,
    })
  })
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

describe('bulkDeleteBackupSources', () => {
  it('preserves a structured active-backup conflict for the caller', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 409,
      message: 'Backup already running',
      data: {
        title: 'Backup already running',
        status: 409,
        code: 'BACKUP.ALREADY_RUNNING',
        meta: {
          task_uuid: 'backup-task-uuid',
          task_type: 'backup',
          status: 'running',
          source_type: 'agent',
          source_ref_id: 25,
        },
      },
    }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    })))

    await expect(bulkDeleteBackupSources(
      ['agent:25'],
      false,
      'DEREGISTER',
      'source-unregister:test',
    )).rejects.toMatchObject({
      status: 409,
      errorCode: 'BACKUP.ALREADY_RUNNING',
      meta: {
        task_uuid: 'backup-task-uuid',
        source_type: 'agent',
        source_ref_id: 25,
      },
    })
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
