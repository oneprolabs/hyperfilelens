import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { computed } from 'vue'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import { operationalEventDisplay, operationalSearchAliases, taskAlertDisplay, taskAlertNotificationDisplay } from './operationalEventDisplay'

const zh = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'), 'utf8',
))
const es = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/es/frontend/messages.json'), 'utf8',
))
const repositoryTask = zh.ops.events.repositoryTask
const repositoryOperations = zh.ops.events.repositoryOperations
const event = {
  event_type: 'task.failed',
  title: 'Repository operation failed: Orphan snapshot reconciliation · huawei-obs-backup-repo',
  details: 'Snapshot reconciliation failed; no unconfirmed deletion will be retried.',
}

function translator(locale = 'zh-hans') {
  return createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: { en, 'zh-hans': zh, es } }).global
}

describe('operational event localization', () => {
  it.each(['en', 'zh-hans', 'es'])('maps visible %s labels to searchable original titles and preserves resource names', (locale) => {
    const { t } = translator(locale)
    const name = 'Backup PROD-team · daily'
    const prefix = t('ops.events.taskTerminal.failed', { type: t('ops.task.taskType.backup'), name: '' }).trim()
    const query = `${prefix}${name}`
    const aliases = operationalSearchAliases(query, t, 'event')
    expect(aliases).toContain(`Backup failed: ${name}`)
    const alertQuery = t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_failed') })
    expect(operationalSearchAliases(alertQuery, t, 'alert')).toContain('Task alert: task_failed')
    expect(operationalSearchAliases('', t, 'alert')).toEqual([])
    expect(operationalSearchAliases('root@PROD-team:/opt/seedone', t, 'event')).toEqual([])
  })

  it('maps partial translated status labels without truncating the set of supported event types', () => {
    const { t } = translator()
    const aliases = operationalSearchAliases(t('ops.task.status.failed'), t, 'event')
    expect(aliases).toContain('Backup failed:')
    expect(aliases).toContain('Restore failed:')
    expect(aliases).toContain('Repository operation failed:')
    expect(aliases.length).toBeLessThanOrEqual(64)
    expect(aliases.every(alias => alias.length <= 200 && alias.trim())).toBe(true)
  })

  it.each(['zh-hans', 'es'])('keeps new %s translations and source locks aligned', (locale) => {
    const messages = locale === 'zh-hans' ? zh : es
    const sourceLock = JSON.parse(readFileSync(resolve(
      process.cwd(), `../../language-packs/packs/${locale}/frontend/source-lock.json`,
    ), 'utf8'))
    const entries: Array<[string, string, string]> = [
      ['taskTerminal.failed', en.ops.events.taskTerminal.failed, messages.ops.events.taskTerminal.failed],
      ['taskTerminal.timeout', en.ops.events.taskTerminal.timeout, messages.ops.events.taskTerminal.timeout],
      ['taskAlert', en.ops.events.taskAlert, messages.ops.events.taskAlert],
      ['resolvedTaskAlert', en.ops.events.resolvedTaskAlert, messages.ops.events.resolvedTaskAlert],
      ['taskDiagnostic.taskStatus', en.ops.events.taskDiagnostic.taskStatus, messages.ops.events.taskDiagnostic.taskStatus],
      ['taskDiagnostic.unsupportedSource', en.ops.events.taskDiagnostic.unsupportedSource, messages.ops.events.taskDiagnostic.unsupportedSource],
    ]
    for (const [path, english, translated] of entries) {
      expect(translated).not.toBe(english)
      expect([...translated.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map(match => match[1]).sort())
        .toEqual([...english.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map(match => match[1]).sort())
      expect(sourceLock[`ops.events.${path}`])
        .toBe(createHash('sha256').update(JSON.stringify(english)).digest('hex'))
    }
  })

  it.each(['en', 'zh-hans', 'es'])('localizes default task explanations and resolved notifications in %s', (locale) => {
    const { t } = translator(locale)
    const id = '68b61992-8307-4cb9-a9a0-89d5bbb58ce5'
    for (const status of ['failed', 'timeout']) {
      const title = `Task alert: task_${status}`
      const message = `Task ${id} status=${status}`
      const expected = t('ops.events.taskDiagnostic.taskStatus', { id, status: t(`ops.task.status.${status}`) })
      const alert = taskAlertDisplay({ type: 'task', title, message }, t)
      expect(alert.message).toBe(expected)
      expect(alert.message).toContain(id)
      const notification = { kind: 'alert', title: `Resolved: ${title}`, summary: message }
      expect(taskAlertNotificationDisplay(notification, t)).toEqual({
        title: t('ops.events.resolvedTaskAlert', { title: alert.title }), summary: expected,
      })
      expect(notification.summary).toBe(message)
      expect(taskAlertNotificationDisplay({ ...notification, title }, t))
        .toEqual({ title: alert.title, summary: expected })
    }
  })

  it('preserves resource names, custom notifications and unrelated sources', () => {
    const { t } = translator()
    for (const title of ['Task alert: task_failed', 'Resolved: Task alert: task_timeout']) {
      expect(taskAlertNotificationDisplay({ kind: 'alert', title, summary: 'Backup prod-myteamone' }, t).summary)
        .toBe('Backup prod-myteamone')
      expect(taskAlertNotificationDisplay({ kind: 'system', title, summary: 'Custom summary' }, t))
        .toEqual({ title, summary: 'Custom summary' })
    }
    for (const title of ['Custom alert', 'Resolved: Custom alert', 'Task alert: future_event']) {
      const summary = 'upload error: unsupported source: root@agent:/data'
      expect(taskAlertNotificationDisplay({ kind: 'alert', title, summary }, t)).toEqual({ title, summary })
    }
    const message = 'Task not-a-uuid status=failed'
    expect(taskAlertDisplay({ type: 'task', message }, t).message).toBe(message)
  })

  it.each(['en', 'zh-hans', 'es'])('localizes historical backup events and alerts in %s', (locale) => {
    const { t } = translator(locale)
    const name = 'Backup prod-myteamone · failed: daily'
    const source = 'root@prod-myteamone:/opt/seedone'
    const details = `upload error: unsupported source: ${source}`
    const original = { event_type: 'task.failed', title: `Backup failed: ${name}`, details }
    const display = operationalEventDisplay(original, t)
    expect(display).toEqual({
      title: t('ops.events.taskTerminal.failed', { type: t('ops.task.taskType.backup'), name }),
      details: t('ops.events.taskDiagnostic.unsupportedSource', { source }),
    })
    expect(display.title).toContain(name)
    expect(display.details).toContain(source)
    expect(original.details).toBe(details)
    expect(taskAlertDisplay({ type: 'task', title: 'Task alert: task_failed', message: details }, t)).toEqual({
      title: t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_failed') }),
      message: display.details,
    })
    if (locale === 'en') expect(display).toEqual({ title: original.title, details })
  })

  it('reacts to locale changes for task events and alerts without mutating evidence', () => {
    const i18n = translator('en')
    const task = { event_type: 'task.timeout', title: 'Restore timeout: restore daily', details: 'Unknown diagnostic' }
    const alert = { type: 'task', title: 'Task alert: task_timeout', message: 'Unknown diagnostic' }
    const displayEvent = computed(() => operationalEventDisplay(task, i18n.t))
    const displayAlert = computed(() => taskAlertDisplay(alert, i18n.t))
    const englishTitle = displayAlert.value.title
    expect(displayEvent.value.title).toBe(task.title)
    i18n.locale.value = 'zh-hans'
    expect(displayEvent.value.title).toBe(i18n.t('ops.events.taskTerminal.timeout', {
      type: i18n.t('ops.task.taskType.restore'), name: 'restore daily',
    }))
    expect(displayAlert.value.title).not.toBe(englishTitle)
    expect(displayAlert.value.message).toBe(alert.message)
    expect(displayEvent.value.details).toBe(task.details)
  })

  it.each(['task_failed', 'task_timeout', 'retry_exceeded', 'partial_success'])('localizes task alert %s', (event) => {
    const { t } = translator()
    expect(taskAlertDisplay({ type: 'task', title: `Task alert: ${event}` }, t).title)
      .toBe(t('ops.events.taskAlert', { event: t(`ops.alertsCenter.taskEvents.${event}`) }))
  })

  it('preserves custom titles, unknown diagnostics, unrelated records and multiline evidence', () => {
    const { t } = translator()
    const message = 'upload error: unsupported source: /data\nEngine diagnostic: exit 7'
    for (const type of ['task', 'event', undefined]) {
      expect(taskAlertDisplay({ type, title: 'Custom alert name', message }, t))
        .toEqual({ title: 'Custom alert name', message })
    }
    const details = 'upload error: unsupported source: /data'
    const unrelated = { event_type: 'repository.offline', title: 'Backup failed: custom', details }
    expect(operationalEventDisplay(unrelated, t)).toEqual({ title: unrelated.title, details })
    expect(taskAlertDisplay({ type: 'event', title: 'Task alert: task_failed', message: details }, t))
      .toEqual({ title: 'Task alert: task_failed', message: details })
  })

  it('translates the reported historical event without mutating it', () => {
    const original = { ...event }
    const { t } = translator()
    expect(operationalEventDisplay(event, t)).toEqual({
      title: repositoryTask.failed.replace('{name}', `${repositoryOperations.reconcile} · huawei-obs-backup-repo`),
      details: repositoryTask.reconciliationFailed,
    })
    expect(event).toEqual(original)
  })

  it('preserves the English text and reacts to locale switching without refetching', () => {
    const i18n = translator('en')
    const display = computed(() => operationalEventDisplay(event, i18n.t))
    expect(display.value).toEqual({ title: event.title, details: event.details })
    i18n.locale.value = 'zh-hans'
    expect(display.value.title).toBe(
      repositoryTask.failed.replace('{name}', `${repositoryOperations.reconcile} · huawei-obs-backup-repo`),
    )
  })

  it.each([
    'Quick maintenance', 'Full maintenance', 'Orphan snapshot reconciliation',
    'Delete subrepository', 'Delete repository', 'Check', 'Update Credentials',
    'Create repository', 'Bind proxy', 'Remount repository',
  ])('translates %s while preserving the complete resource name', (action) => {
    const { t } = translator()
    const resourceName = `repo · Check: ${repositoryOperations.check}`
    const display = operationalEventDisplay({
      event_type: 'task.timeout',
      title: `Repository operation timeout: ${action} · ${resourceName}`,
      details: 'Unknown engine diagnostic: failed with exit code 7',
    }, t)
    expect(display.title).toMatch(
      new RegExp(`^${repositoryTask.timeout.replace('{name}', '.+')} · ${resourceName}$`),
    )
    expect(display.title).not.toContain(`${action} · repo`)
    expect(display.details).toBe('Unknown engine diagnostic: failed with exit code 7')
  })

  it('retains unknown actions and does not translate unrelated event evidence', () => {
    const { t } = translator()
    expect(operationalEventDisplay({
      ...event, title: 'Repository operation failed: Future operation · repo',
    }, t).title).toBe(repositoryTask.failed.replace('{name}', 'Future operation · repo'))
    for (const event_type of ['task.success', 'repository.offline', undefined]) {
      expect(operationalEventDisplay({ ...event, event_type }, t))
        .toEqual({ title: event.title, details: event.details })
    }
  })

  it('uses the shared formatter in both event surfaces and the details drawer', () => {
    const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')
    const events = readFileSync(resolve(process.cwd(), 'src/pages/ops/Events.vue'), 'utf8')
    expect(dashboard).toContain('v-for="item in displayEvents"')
    expect(dashboard).toContain('operationalEventDisplay({ ...event, details: event.detail }, t)')
    expect(events).toContain(':data="displayEvents"')
    expect(events).toContain('operationalEventDisplay(rawSelectedEvent.value, t)')
    expect(dashboard).toContain('v-for="item in displayAlerts"')
    expect(dashboard).toContain('taskAlertDisplay({ ...alert, message: alert.detail }, t)')
    const alerts = readFileSync(resolve(process.cwd(), 'src/pages/ops/AlertIncidents.vue'), 'utf8')
    expect(alerts).toContain('taskAlertDisplay(row, t).title')
    expect(alerts).toContain('taskAlertDisplay(selected, t).title')
    expect(alerts).toContain('taskAlertDisplay(alert, t).message.trim()')
  })
})
