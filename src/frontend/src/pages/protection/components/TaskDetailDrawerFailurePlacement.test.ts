// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, registerLocale } from '../../../i18n'
import TaskDetailDrawer from './TaskDetailDrawer.vue'
import { getTask, listTaskEvents, type TaskRow, type TaskEventRow } from '../../../lib/taskApi'

vi.mock('../../../lib/taskApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/taskApi')>()),
  getTask: vi.fn(),
  listTaskEvents: vi.fn(),
}))
vi.mock('../../../composables/useDrawerTableMaxHeight', () => ({
  useDrawerTableMaxHeight: () => ({ tableMaxHeight: ref(400), containerRef: ref(null) }),
}))

for (const locale of ['zh-hans', 'es']) {
  registerLocale(locale, JSON.parse(readFileSync(resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8')))
}

const steps = [
  { id: 1, step_index: 1, step_name: 'kopia_snapshot', status: 'success', progress: 100 },
  { id: 2, step_index: 2, step_name: 'finalize_snapshot', status: 'success', progress: 100 },
]
const task = {
  id: 1,
  organization_id: 1,
  task_uuid: 'backup-task',
  task_type: 'backup',
  display_name: 'Backup',
  status: 'success',
  current_step: 'finalize_snapshot',
  progress: 100,
  retry_count: 0,
  recovery_attempt: 0,
  trigger_type: 'manual',
  steps,
  resources: [],
  recent_events: [],
  error_details: {
    version: 1,
    severity: 'warning',
    outcome: 'warning',
    summary: 'Task completed with warnings.',
    reasons: [],
    suggestions: [{ code: 'review_backup_diagnostics', detail: 'Review skipped items.' }],
    skipped_items: { count: 264 },
    task_uuid: 'backup-task',
  },
} satisfies Partial<TaskRow> as TaskRow

function mountDrawer() {
  return mount(TaskDetailDrawer, {
    props: { modelValue: true, taskUuid: 'backup-task' },
    global: {
      plugins: [i18n],
      directives: { loading: {}, 'table-column-resize': {}, 'table-overflow-title': {} },
      stubs: {
        ElDrawer: { template: '<div><slot name="header" /><slot /></div>' },
        ElButton: true,
        ElTag: true,
        ElTabs: { template: '<div><slot /></div>' },
        ElTabPane: { template: '<div><slot /></div>' },
        ElTable: true,
        ElTableColumn: true,
        ElAlert: true,
        ElTooltip: true,
        ElEmpty: true,
        ElSkeleton: true,
      },
    },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  vi.mocked(getTask).mockResolvedValue(task)
  vi.mocked(listTaskEvents).mockResolvedValue({ count: 0, results: [] })
})

afterEach(() => {
  i18n.global.locale.value = 'en'
  vi.clearAllMocks()
})

describe('TaskDetailDrawer failure placement', () => {
  it('renders localized orphan audit details once without event-time badges', async () => {
    i18n.global.locale.value = 'zh-hans'
    const snapshotCreatedAt = '2026-09-21T03:33:44.000Z'
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      task_type: 'repository_operation',
      operation_type: 'snapshot.reconcile',
      error_details: undefined,
      steps: ['prepare_repository_operation', 'verify_repository_owner', 'run_repository_operation', 'finalize_repository_operation'].map((step_name, index) => ({
        id: index + 1, step_index: index + 1, step_name, status: 'success', progress: 100,
      })),
    })
    const events: TaskEventRow[] = [
      { id: 1, seq: 1, step_id: 1, level: 'INFO', message: 'Task started', created_at: '2026-10-12T01:00:11Z' },
      {
        id: 2, seq: 2, level: 'INFO', message: 'Orphan snapshot discovered',
        created_at: '2026-10-12T01:00:14Z',
        metadata: {
          event_type: 'orphan_snapshot_result', outcome: 'discovered', snapshot_id: 'a'.repeat(32),
          snapshot_created_at: snapshotCreatedAt,
          source: { name: 'Unlinked HFL backup source', host: 'host', user: 'root', path: '/backup' },
          reason: 'Automatic orphan deletion is disabled.',
        },
      },
      {
        id: 3, seq: 3, level: 'INFO', message: 'Orphan snapshot reconciliation summary',
        created_at: '2026-10-12T01:00:14Z',
        metadata: { event_type: 'orphan_snapshot_summary', summary: { scanned: 1, discovered: 1 } },
      },
    ]
    vi.mocked(listTaskEvents).mockResolvedValue({ count: events.length, results: events })
    const wrapper = mountDrawer()
    await flushPromises()
    const result = wrapper.get('.orphan-snapshot-result')
    expect(result.find('strong').exists()).toBe(false)
    expect(i18n.global.te('ops.task.orphanSnapshots.snapshotCreatedAt', 'zh-hans')).toBe(true)
    expect(result.text()).toContain(i18n.global.t('ops.task.orphanSnapshots.snapshotCreatedAt'))
    expect(result.text()).toContain('2026-09-21')
    expect(result.text()).toContain(i18n.global.t('ops.task.orphanSnapshots.unlinkedSource'))
    expect(result.text()).toContain(i18n.global.t('ops.task.orphanSnapshots.deletionDisabled'))
    expect(wrapper.text()).not.toContain('Orphan snapshot discovered')
    expect(wrapper.text()).not.toContain('Orphan snapshot reconciliation summary')
    expect(wrapper.text()).toContain(i18n.global.t('ops.task.orphanSnapshots.summary'))
    expect(wrapper.findAll('.hfl-task-drawer__event-time')).toHaveLength(1) // Normal task event remains timestamped.
    wrapper.unmount()
  })

  for (const locale of ['en', 'zh-hans', 'es']) {
    it(`localizes offline deregistration in ${locale}, including historical task guidance`, async () => {
      i18n.global.locale.value = locale
      const t = i18n.global.t
      const reason = {
        code: 'agent_offline',
        detail: 'Agent "Test Host" is offline — remote uninstall is required in strict mode.',
      }
      vi.mocked(getTask).mockResolvedValue({
        ...task,
        task_type: 'source_unregister',
        status: 'failed',
        steps: [{ ...steps[0], step_name: 'cleanup_source_endpoint', status: 'failed' }],
        error_details: {
          ...task.error_details!,
          severity: 'error', outcome: 'failed',
          summary: 'Backup source was not deleted.',
          reasons: [reason],
          suggestions: [{ code: 'review_task', detail: 'Old English fallback' }],
        },
      })
      vi.mocked(listTaskEvents).mockResolvedValue({ count: 1, results: [{
        id: 1, step_id: 1, seq: 1, level: 'ERROR',
        message: 'Backup source was not deleted.',
        metadata: {
          error_code: 'SOURCE_UNREGISTER_PREFLIGHT_FAILED',
          error_message: 'Backup source was not deleted.',
        },
      }] })
      const wrapper = mountDrawer()
      await flushPromises()
      const box = wrapper.get('.task-event-failure__terminal-box')
      expect(box.text()).toContain(t('protection.backupsPage.unregisterStrictOfflineNamed', { name: 'Test Host' }))
      expect(box.text()).toContain(t('ops.task.failureDetails.suggestion.reconnect_unregister_agent'))
      expect(wrapper.get('.hfl-task-drawer__event-msg').text())
        .toBe(t('ops.task.eventMessage.backupSourceNotDeleted'))
      expect(box.text()).not.toMatch(/credentials|credenciales/)
      expect(box.get('.task-event-failure__summary--terminal').text())
        .not.toContain('Backup source was not deleted.')
      expect(box.text()).toContain('Backup source was not deleted.') // original technical evidence
      wrapper.unmount()
    })
  }

  it('uses structured legacy payload reasons when the task has no error contract or events', async () => {
    i18n.global.locale.value = 'zh-hans'
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      task_type: 'source_unregister', status: 'failed',
      steps: [{ ...steps[0], step_name: 'cleanup_source_endpoint', status: 'failed' }],
      error_details: null,
      error_message: 'Backup source was not deleted.',
      result_payload: {
        reasons: [{ code: 'agent_offline', detail: 'Agent "Test Host" is offline — remote uninstall is required in strict mode.' }],
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.text()).toContain('Test Host')
    expect(wrapper.text()).toContain(i18n.global.t('protection.backupsPage.unregisterStrictOfflineNamed', { name: 'Test Host' }))
    expect(wrapper.text()).toContain(i18n.global.t('ops.task.failureDetails.suggestion.reconnect_unregister_agent'))
    expect(wrapper.get('.hfl-task-drawer__failure-list').text())
      .not.toMatch(/\[object Object\]|Backup source was not deleted/)
    wrapper.unmount()
  })

  it('moves the synthetic aggregate error to collapsed, sanitized technical details', async () => {
    i18n.global.locale.value = 'zh-hans'
    const diagnostic = 'Synthetic test failure: Agent offline; remote uninstall could not complete. No real Agent was contacted.'
    vi.mocked(getTask).mockResolvedValue({
      ...task, task_type: 'source_unregister', status: 'failed', steps: [],
      error_code: 'SOURCE_UNREGISTER_FAILED', error_message: diagnostic,
      error_details: {
        ...task.error_details!, severity: 'error', outcome: 'failed',
        error_code: 'SOURCE_UNREGISTER_FAILED', summary: 'Task failed.',
        reasons: [
          { code: 'agent_offline', detail: 'Agent offline' },
          { code: 'SOURCE_UNREGISTER_FAILED', detail: diagnostic },
        ],
        suggestions: [{ code: 'review_source_unregister', detail: '' }],
        technical_detail: { password: 'secret-test-password' },
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    const panel = wrapper.get('.hfl-task-drawer__event-failure-panel--stacked')
    expect(panel.text()).toContain(i18n.global.t('ops.task.failureDetails.reason.agent_offline'))
    expect(panel.text()).toContain(i18n.global.t('ops.task.failureDetails.suggestion.reconnect_unregister_agent'))
    expect(panel.text()).not.toMatch(/Synthetic test failure|Bring the Agent online/)
    const technical = wrapper.get('.hfl-error-technical-details')
    expect(technical.attributes('open')).toBeUndefined()
    expect(technical.text()).toContain(diagnostic)
    expect(technical.text()).not.toContain('secret-test-password')
    wrapper.unmount()
  })
  it('does not put a successful backup warning into a red Task failed panel', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    wrapper.unmount()
  })

  it('attaches a failure fallback only to the step actually marked failed', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
        failed_step: 'finalize_snapshot', // stale hint must not override step status
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    const cards = wrapper.findAll('.hfl-task-drawer__step-card')
    expect(cards[0].findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(1)
    expect(cards[1].findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(0)
    wrapper.unmount()
  })

  it('uses one unattributed task fallback when no failed step or directory event exists', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__step-card .hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(1)
    expect(wrapper.text()).toContain('Could not determine which step failed.')
    wrapper.unmount()
  })

  it('does not duplicate a directory-specific failure at the step or task level', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    const event: TaskEventRow = {
      id: 3,
      step_id: 1,
      seq: 3,
      level: 'ERROR',
      message: 'Directory backup failed',
      metadata: { error_code: 'KOPIA_PROCESS_DIED', error_message: 'Source read failed' },
    }
    vi.mocked(listTaskEvents).mockResolvedValue({ count: 1, results: [event] })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__terminal-box')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not duplicate an explicit non-directory error event', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 4,
        step_id: 1,
        seq: 4,
        level: 'ERROR',
        message: 'Repository connection failed',
        metadata: { error_code: 'REPOSITORY_UNAVAILABLE', error_message: 'Repository unavailable' },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__terminal-box')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows one standard panel and controller-specific guidance for a recovered maintenance failure', async () => {
    const message = 'Controller repository maintenance lost its execution heartbeat after a control-plane interruption.'
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      task_type: 'repository_operation',
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
        suggestions: [{ code: 'recover_controller_interruption', detail: 'recover_controller_interruption' }],
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 6, step_id: 1, seq: 6, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_code: 'CONTROL_PLANE_RESTART_INTERRUPTED', error_message: message },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__terminal-box')).toHaveLength(1)
    expect(wrapper.get('.task-event-failure__summary--terminal').text()).toBe(message)
    expect(wrapper.get('.task-event-failure__remediation-list').text()).toContain('Scheduled maintenance will retry automatically')
    expect(wrapper.get('.task-event-failure__remediation-list').text()).not.toContain('repository configuration')
    expect(wrapper.text()).not.toContain('Review the repository configuration')
    expect(wrapper.find('.task-event-failure__technical').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the generic fallback when an error message has no standard error code', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: { ...task.error_details!, severity: 'error', outcome: 'failed' },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 7, step_id: 1, seq: 7, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_message: 'Unclassified failure' },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(1)
    expect(wrapper.find('.task-event-failure').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(
    ['en', 'zh-hans', 'es'].flatMap(locale =>
      ['proxy', 'backup_host', 'host'].map(role => ({ locale, role })),
    ),
  )('localizes the $role connection failure in $locale and retains raw diagnostics', async ({ locale, role }) => {
    i18n.global.locale.value = locale
    const rawError = 'agent websocket is not routable'
    const reasonCode = `repository_${role}_unreachable`
    const suggestionCode = `reconnect_repository_${role}`
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      task_type: 'repository_operation',
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        reasons: [{ code: reasonCode, detail: 'Server-side English fallback' }],
        suggestions: [{ code: suggestionCode, detail: 'Server-side English fallback' }],
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 9, step_id: 1, seq: 9, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_code: 'REPOSITORY_OPERATION_FAILED', error_message: rawError },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.get('.task-event-failure__summary--terminal').text())
      .toBe(i18n.global.t(`ops.task.failureDetails.reason.${reasonCode}`))
    const resolution = wrapper.get('.task-event-failure__remediation-list').text()
    expect(resolution).toBe(i18n.global.t(`ops.task.failureDetails.suggestion.${suggestionCode}`))
    expect(resolution).not.toContain('Agent')
    expect(wrapper.get('.task-event-failure__summary--terminal').text()).not.toContain('Agent')
    expect(resolution).not.toContain('repository configuration')
    expect(wrapper.text()).not.toContain('Server-side English fallback')
    expect(wrapper.get('.task-event-failure__technical pre').text()).toBe(rawError)
    expect(wrapper.get('.task-event-failure__technical').attributes('open')).toBeUndefined()
    wrapper.unmount()
  })

  it('anchors a task-level fallback when an event has only an error code', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: { ...task.error_details!, severity: 'error', outcome: 'failed' },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 8, step_id: 1, seq: 8, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_code: 'UNKNOWN_FAILURE' },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(1)
    wrapper.unmount()
  })

  it('hides stale skipped samples when a failed task explicitly has zero skipped items', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      result_payload: { skipped_item_count: 0, skipped_file_count: 0, skipped_directory_count: 0 },
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 5,
        step_id: 1,
        seq: 5,
        level: 'ERROR',
        message: 'Directory backup failed',
        metadata: {
          error_code: 'SOURCE_ITEMS_UNREADABLE',
          failure_details: {
            count: 189,
            items: [{ path: '/DumpStack.log.tmp', error: 'device or resource busy' }],
          },
          skipped_details: {
            count: 189,
            items: [{ path: '/DumpStack.log.tmp', error: 'device or resource busy' }],
          },
        },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.text()).not.toContain('189 source items were skipped')
    expect(wrapper.findAll('.task-event-failure__summary--warning')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__summary:not(.task-event-failure__summary--warning)')).not.toHaveLength(0)
    wrapper.unmount()
  })
})
