// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import { notifyUnregisterTaskSuccess } from './sourceUnregisterFeedback'
import { resetToastStoreForTests, toastState } from './toast/store'
import type { TaskRow } from './taskApi'

const t = createI18n({ legacy: false, locale: 'en', messages: { en } }).global.t
function task(uuid: string, overrides: Partial<TaskRow> = {}): TaskRow {
  return { task_uuid: uuid, status: 'success', result_payload: { result: 'completed', cleanup_complete: true }, ...overrides } as TaskRow
}

beforeEach(() => {
  resetToastStoreForTests()
  sessionStorage.clear()
  localStorage.clear()
})

describe('unregister completion feedback', () => {
  it('confirms a single source and does not repeat after dismissal or a locale change', () => {
    const tasks = [task('nas-completed')]
    notifyUnregisterTaskSuccess({ t, tasks, sourceName: 'backup_nfs' })
    expect(toastState.items).toHaveLength(1)
    expect(toastState.items[0]).toMatchObject({ type: 'success', message: 'Backup source "backup_nfs" deregistered.' })
    resetToastStoreForTests()
    const zhT = createI18n({ legacy: false, locale: 'zh', messages: { zh } }).global.t
    notifyUnregisterTaskSuccess({ t: zhT, tasks, sourceName: 'backup_nfs' })
    expect(toastState.items).toHaveLength(0)
  })

  it('waits for the entire batch and emits one count-based result', () => {
    notifyUnregisterTaskSuccess({ t, tasks: [task('first'), task('second', { status: 'running' })] })
    expect(toastState.items).toHaveLength(0)
    notifyUnregisterTaskSuccess({ t, tasks: [task('first'), task('second')] })
    expect(toastState.items[0].message).toBe('2 backup source(s) deregistered.')
    resetToastStoreForTests()
    notifyUnregisterTaskSuccess({ t, tasks: [task('second'), task('first')] })
    expect(toastState.items).toHaveLength(0)
  })

  it.each(['pending', 'running', 'waiting', 'blocked', 'failed', 'cancelled', 'timeout', 'partial'])(
    'does not claim success for %s',
    status => {
      notifyUnregisterTaskSuccess({ t, tasks: [task('not-clean', { status: status as TaskRow['status'] })] })
      expect(toastState.items).toHaveLength(0)
    },
  )

  it.each([
    { result: 'partial_success' },
    { cleanup_complete: false },
    { pending_removals: [{ source_id: 'agent:1', node_id: 1 }] },
    { retained_resources: ['agent_installation'] },
    { warnings: [{ code: 'warning', detail: 'Needs review' }] },
    { cleanup_failures: [{ code: 'failed', detail: 'Unmount failed' }] },
  ])('does not claim clean success with residue %j', result_payload => {
    notifyUnregisterTaskSuccess({ t, tasks: [task('residue', { result_payload })] })
    expect(toastState.items).toHaveLength(0)
  })

  it('does not claim batch success when a target was rejected or unavailable', () => {
    notifyUnregisterTaskSuccess({ t, tasks: [task('accepted')], hasRejections: true })
    notifyUnregisterTaskSuccess({ t, tasks: [] })
    expect(toastState.items).toHaveLength(0)
  })

  it('localizes Chinese feedback and scopes deduplication by organization', () => {
    const zhT = createI18n({ legacy: false, locale: 'zh', messages: { zh } }).global.t
    localStorage.setItem('hfl_org_key', 'org-a')
    notifyUnregisterTaskSuccess({ t: zhT, tasks: [task('scoped')], sourceName: 'Test source' })
    expect(toastState.items[0].message).toBe(zh.protection.backupsPage.msgDeleteSourceSuccessNamed.replace('{name}', 'Test source'))
    resetToastStoreForTests()
    localStorage.setItem('hfl_org_key', 'org-b')
    notifyUnregisterTaskSuccess({ t: zhT, tasks: [task('scoped')] })
    expect(toastState.items[0].message).toBe(zh.protection.backupsPage.msgDeleteSourceSuccessCount.replace('{n}', '1'))
  })
})
