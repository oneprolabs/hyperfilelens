// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import zhHans from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import { getNode } from './nodeApi'
import { getSourceResource } from './sourceApi'
import { getTask, listTasks, type TaskRow } from './taskApi'
import { isSourceFailure, latestUnregisterFailure, resolveSourceFailureDetails } from './sourceFailureDiagnostics'

vi.mock('./nodeApi', () => ({ getNode: vi.fn() }))
vi.mock('./sourceApi', async importOriginal => ({
  ...await importOriginal<typeof import('./sourceApi')>(),
  getSourceResource: vi.fn(),
}))
vi.mock('./taskApi', () => ({ getTask: vi.fn(), listTasks: vi.fn() }))
const t = ((key: string) => key) as never
const task = (changes: Partial<TaskRow> = {}) => ({
  task_uuid: 'unregister-55',
  task_type: 'source_unregister',
  status: 'failed',
  error_message: 'Remote uninstall failed',
  resources: [{ resource_type: 'backup_source', resource_subtype: 'agent', resource_id: 55 }],
  ...changes,
}) as TaskRow

beforeEach(() => { vi.resetAllMocks() })

describe('source failure diagnostics', () => {
  it('recognizes failures without treating offline or active states as errors', () => {
    for (const state of ['error', 'failed', 'upgrade_failed', 'deregistration_failed', 'remove_failed', 'provision_failed']) {
      expect(isSourceFailure(state)).toBe(true)
    }
    for (const state of ['offline', 'active', 'removing', 'verification_pending', undefined]) {
      expect(isSourceFailure(state)).toBe(false)
    }
  })

  it('recovers a persisted failure without browser storage using exact typed source identity', async () => {
    vi.mocked(listTasks).mockResolvedValue({ count: 1, results: [task()] })
    vi.mocked(getTask).mockResolvedValue(task())
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'deregistration_failed' })
    expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({
      task_type: 'source_unregister', resource_type: 'backup_source', resource_subtype: 'agent',
      resource_id: 55, page_size: 1,
    }), undefined)
    expect(details.taskUuid).toBe('unregister-55')
    expect(details.reasons).toContain('Remote uninstall failed')
    expect(getNode).not.toHaveBeenCalled()
  })

  it.each(['success', 'running', 'waiting', 'blocked', 'cancelled'])(
    'does not resurrect an old failure after the latest task is %s',
    async status => {
      vi.mocked(listTasks).mockResolvedValue({ count: 1, results: [task({ status })] })
      const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'deregistration_failed' })
      expect(details.taskUuid).toBeUndefined()
      expect(getTask).not.toHaveBeenCalled()
      expect(getNode).not.toHaveBeenCalled()
    },
  )

  it('rechecks task status after list retrieval', async () => {
    vi.mocked(listTasks).mockResolvedValue({ count: 1, results: [task()] })
    vi.mocked(getTask).mockResolvedValue(task({ status: 'success' }))
    expect(await latestUnregisterFailure('agent:55')).toBeNull()
  })

  it('does not confuse NAS and Agent with the same numeric ID', async () => {
    vi.mocked(listTasks).mockResolvedValue({ count: 1, results: [task()] })
    vi.mocked(getTask).mockResolvedValue(task())
    expect(await latestUnregisterFailure('nas:55')).toBeNull()
    expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ resource_subtype: 'nas' }), undefined)
  })

  it('reads upgrade lifecycle identity and opens only its own upgrade task', async () => {
    vi.mocked(getNode).mockResolvedValue({
      id: 55, lifecycle: { kind: 'upgrade', state: 'failed', task_uuid: 'upgrade-55', error: 'Download failed' },
    } as Awaited<ReturnType<typeof getNode>>)
    vi.mocked(getTask).mockResolvedValue(task({
      task_uuid: 'upgrade-55', task_type: 'node_lifecycle',
      request_payload: { operation: 'upgrade' },
      resources: [{ resource_type: 'host', resource_subtype: 'agent', resource_id: 55 }],
    }))
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'upgrade_failed' })
    expect(details.taskUuid).toBe('upgrade-55')
    expect(details.taskType).toBe('node_lifecycle')
    expect(listTasks).not.toHaveBeenCalled()
  })

  it('does not guess that a generic failure was an unregister operation', async () => {
    vi.mocked(getNode).mockResolvedValue({ id: 55, status: 'failed' } as Awaited<ReturnType<typeof getNode>>)
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'failed' })
    expect(details.summary).toContain('sourceFailureDetailsUnavailable')
    expect(listTasks).not.toHaveBeenCalled()
    expect(getTask).not.toHaveBeenCalled()
  })

  it('does not display remove details for an upgrade failure', async () => {
    vi.mocked(getNode).mockResolvedValue({
      id: 55, lifecycle: { kind: 'remove', state: 'failed', task_uuid: 'unregister-55' },
    } as Awaited<ReturnType<typeof getNode>>)
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'upgrade_failed' })
    expect(details.taskUuid).toBeUndefined()
    expect(getTask).not.toHaveBeenCalled()
  })

  it.each(['success', 'running', 'cancelled'])('does not revive stale lifecycle errors when the associated task is %s', async status => {
    vi.mocked(getNode).mockResolvedValue({
      id: 55, lifecycle: { kind: 'upgrade', state: 'failed', task_uuid: 'upgrade-55', error: 'Old failure' },
    } as Awaited<ReturnType<typeof getNode>>)
    vi.mocked(getTask).mockResolvedValue(task({
      task_uuid: 'upgrade-55', task_type: 'node_lifecycle', status,
      request_payload: { operation: 'upgrade' },
      resources: [{ resource_type: 'host', resource_subtype: 'agent', resource_id: 55 }],
    }))
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'upgrade_failed' })
    expect(details.taskUuid).toBeUndefined()
    expect(details.reasons).not.toContain('Old failure')
  })

  it.each([
    ['provision_failed', 'backup_config_provision'],
    ['reset_failed', 'backup_config_reset'],
  ])('resolves %s using only the matching configuration operation', async (status, taskType) => {
    vi.mocked(listTasks).mockResolvedValue({ count: 1, results: [task({ task_type: taskType })] })
    vi.mocked(getTask).mockResolvedValue(task({ task_type: taskType }))
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status })
    expect(details.taskType).toBe(taskType)
    expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ task_type: taskType }), undefined)
    expect(getNode).not.toHaveBeenCalled()
  })

  it('uses legacy lifecycle diagnostics without inventing a task', async () => {
    vi.mocked(getNode).mockResolvedValue({
      id: 55, lifecycle: { kind: 'upgrade', state: 'failed', error: 'Checksum mismatch', failure_code: 'CHECKSUM_FAILED' },
    } as Awaited<ReturnType<typeof getNode>>)
    const details = await resolveSourceFailureDetails({ t, sourceId: 'agent:55', sourceName: 'fixture', status: 'upgrade_failed' })
    expect(details.reasons).toEqual(['Checksum mismatch'])
    expect(details.errorCode).toBe('CHECKSUM_FAILED')
    expect(details.taskUuid).toBeUndefined()
  })

  it('reads NAS diagnostics without querying unrelated node tasks', async () => {
    vi.mocked(getSourceResource).mockResolvedValue({ id: 55, effective_status_message: 'Mount permission denied' } as Awaited<ReturnType<typeof getSourceResource>>)
    const details = await resolveSourceFailureDetails({ t, sourceId: 'nas:55', sourceName: 'fixture', status: 'error' })
    expect(details.reasons).toEqual(['Mount permission denied'])
    expect(getNode).not.toHaveBeenCalled()
    expect(listTasks).not.toHaveBeenCalled()
  })

  it('propagates request errors rather than presenting them as no history', async () => {
    vi.mocked(listTasks).mockRejectedValue(new Error('Network unavailable'))
    await expect(latestUnregisterFailure('agent:55')).rejects.toThrow('Network unavailable')
  })

  it.each(['en', 'zh-hans', 'es'])('localizes upgrade summary, coded reasons and suggestions in %s', async locale => {
    const i18n = createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: { en, 'zh-hans': zhHans, es } })
    const translate = i18n.global.t as unknown as typeof t
    vi.mocked(getNode).mockResolvedValue({
      id: 55, lifecycle: { kind: 'upgrade', state: 'failed', task_uuid: 'upgrade-55' },
    } as Awaited<ReturnType<typeof getNode>>)
    vi.mocked(getTask).mockResolvedValue(task({
      task_type: 'node_lifecycle', request_payload: { operation: 'upgrade' },
      resources: [{ resource_type: 'host', resource_subtype: 'agent', resource_id: 55 }],
      error_details: {
        version: 1, severity: 'error', outcome: 'failed', task_uuid: 'upgrade-55',
        summary: 'Task failed.',
        reasons: [{ code: 'agent_offline', detail: 'English backend fallback.' }],
        suggestions: [{ code: 'review_task', detail: 'review_task' }],
      },
    }))
    const details = await resolveSourceFailureDetails({
      t: translate, sourceId: 'agent:55', sourceName: 'fixture', status: 'upgrade_failed',
    })
    expect(details.summary).toBe(i18n.global.t('protection.backupsPage.sourceFailureSummary', {
      name: 'fixture', status: i18n.global.t('nodeLifecycle.state.upgrade_failed'),
    }))
    expect(details.reasons).toEqual([i18n.global.t('ops.task.failureDetails.reason.agent_offline')])
    expect(details.resolutions).toEqual([i18n.global.t('ops.task.failureDetails.suggestion.review_task')])
    expect(details.summary).not.toBe('Task failed.')
    expect(JSON.stringify(details.rawDetail)).toContain('English backend fallback.')
  })

  it('localizes legacy package-download diagnostics using their stable failure code', async () => {
    const i18n = createI18n({ legacy: false, locale: 'zh-hans', messages: { 'zh-hans': zhHans } })
    vi.mocked(getNode).mockResolvedValue({
      id: 55, lifecycle: {
        kind: 'upgrade', state: 'failed', failure_code: 'AGENT_PACKAGE_DOWNLOAD_INTERRUPTED',
        error: 'Package download interrupted',
      },
    } as Awaited<ReturnType<typeof getNode>>)
    const details = await resolveSourceFailureDetails({
      t: i18n.global.t as unknown as typeof t, sourceId: 'agent:55', sourceName: 'fixture', status: 'upgrade_failed',
    })
    expect(details.reasons).toEqual([i18n.global.t('nodeUpgradeProgress.packageDownloadInterrupted')])
    expect(details.reasons?.join()).not.toContain('Package download interrupted')
    expect(JSON.stringify(details.rawDetail)).toContain('Package download interrupted')
  })

  it('preserves unknown technical evidence rather than replacing it with an unrelated translation', async () => {
    const i18n = createI18n({ legacy: false, locale: 'zh-hans', messages: { 'zh-hans': zhHans } })
    vi.mocked(getSourceResource).mockResolvedValue({
      id: 55, effective_status_message: 'Unknown vendor error 7123: /srv/source',
    } as Awaited<ReturnType<typeof getSourceResource>>)
    const details = await resolveSourceFailureDetails({
      t: i18n.global.t as unknown as typeof t, sourceId: 'nas:55', sourceName: 'fixture', status: 'error',
    })
    expect(details.reasons).toEqual(['Unknown vendor error 7123: /srv/source'])
    expect(details.summary).toContain('任务失败')
  })
})
