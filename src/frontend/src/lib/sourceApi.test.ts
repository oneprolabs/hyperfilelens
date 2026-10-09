// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  bulkDeleteBackupSources,
  createBackupSourceDirectory,
  listBackupSelectableSources,
  listSourcesForTag,
  listSourceTagFilterOptions,
  createSourceTag,
  bulkUpdateSourceTags,
  updateSourceTag,
  productionSourceSummary,
  testSourceDraft,
} from './sourceApi'


afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('source tag editing', () => {
  it('loads tag totals and the visible untagged source count for filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      results: [{ id: 2, name: 'Production', description: '', color: 'blue', source_count: 5 }],
      untagged_source_count: 12,
    }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listSourceTagFilterOptions()).resolves.toEqual({
      tags: [{ id: 2, name: 'Production', description: '', color: 'blue', source_count: 5 }],
      untaggedSourceCount: 12,
    })
  })

  it('submits name, description, and palette color in both add and edit', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      id: 7, name: 'Production', description: 'Critical services', color: 'purple',
    }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const input = { name: 'Production', description: 'Critical services', color: 'purple' as const }

    await createSourceTag(input)
    await updateSourceTag(7, input)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/source/tags/',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(input) }),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/source/tags/7/',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify(input) }),
    )
  })

  it('sends incremental tag operations without replacing other labels', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      sources: 2, added: 2, removed: 0,
    }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const payload = { operation: 'add' as const, source_ids: ['agent:1', 'nas:2'], tag_ids: [7] }
    await expect(bulkUpdateSourceTags(payload)).resolves.toEqual({ sources: 2, added: 2, removed: 0 })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/source/tags/assignments/bulk/',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(payload) }),
    )
  })

  it('loads the server-paginated sources bound to a tag', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      count: 1, results: [{ id: 'agent:9', name: 'Host', type: 'host', availability: 'online' }],
    }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(listSourcesForTag(7, { page: 2, page_size: 30, search: 'Host' }))
      .resolves.toMatchObject({ count: 1, results: [{ id: 'agent:9' }] })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/source/tags/7/sources/?page=2&page_size=30&search=Host',
      expect.anything(),
    )
  })
})

describe('createBackupSourceDirectory', () => {
  it('serializes multiple tag filters as comma-separated IDs and untagged state', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      count: 0, results: [],
    }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await listBackupSelectableSources({ tag_ids: [3, 8], untagged: false })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/source/backup-selectable/?tag_ids=3%2C8&untagged=false',
      expect.anything(),
    )
  })

  it('posts the selected parent and one child folder name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      source_id: 'agent:12',
      node_id: 12,
      path: '/data/restore_test',
      label: 'restore_test',
      isLeaf: false,
      is_dir: true,
      path_type: 'directory',
      task_id: 'task-1',
    }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(createBackupSourceDirectory({
      source_id: 'agent:12',
      parent_path: '/data',
      name: 'restore_test',
    })).resolves.toMatchObject({ path: '/data/restore_test', path_type: 'directory' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/source/backup-selectable/directories/create/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          source_id: 'agent:12',
          parent_path: '/data',
          name: 'restore_test',
        }),
      }),
    )
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
