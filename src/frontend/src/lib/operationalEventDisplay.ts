type Translate = (key: string, params?: Record<string, unknown>) => string

export type OperationalEventText = {
  event_type?: string
  title?: string
  details?: string
}

const repositoryOperations: Record<string, string> = {
  'Quick maintenance': 'quick',
  'Full maintenance': 'full',
  'Orphan snapshot reconciliation': 'reconcile',
  'Delete subrepository': 'cleanupTarget',
  'Delete repository': 'cleanupRepository',
  'Check': 'check',
  'Update Credentials': 'credentials',
  'Create repository': 'create',
  'Bind proxy': 'bind',
  'Remount repository': 'remount',
}

const taskTypes: Record<string, string> = {
  Backup: 'backup',
  Restore: 'restore',
  'Insight workspace restore': 'insight_workspace_restore',
  'Snapshot download': 'snapshot_download',
  'Snapshot delete': 'snapshot_delete',
  'Backup config reset': 'backup_config_reset',
  'Backup config provision': 'backup_config_provision',
  'Source unregister': 'source_unregister',
  'Node lifecycle': 'node_lifecycle',
  'Storage provider validation': 'storage_provider_validation',
}

function taskDiagnosticDisplay(details: string, t: Translate) {
  const taskStatus = /^Task ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) status=(failed|timeout)$/i.exec(details)
  if (taskStatus) {
    return t('ops.events.taskDiagnostic.taskStatus', {
      id: taskStatus[1], status: t(`ops.task.status.${taskStatus[2]!.toLowerCase()}`),
    })
  }
  const unsupportedSource = /^upload error: unsupported source: ([^\r\n]+)$/.exec(details)
  return unsupportedSource
    ? t('ops.events.taskDiagnostic.unsupportedSource', { source: unsupportedSource[1] })
    : details
}

const taskAlertTitlePattern = /^Task alert: (task_failed|task_timeout|retry_exceeded|partial_success)$/

export function operationalSearchAliases(search: string, t: Translate, kind: 'event' | 'alert'): string[] {
  const query = search.trim().toLowerCase()
  if (!query) return []
  const aliases = new Set<string>()
  function add(localized: string, original: string) {
    const label = localized.trim().toLowerCase()
    if (!label) return
    if (label.includes(query)) aliases.add(original.trim())
    else if (query.startsWith(label)) aliases.add(original + search.trim().slice(label.length).trimStart())
  }
  if (kind === 'alert') {
    for (const event of ['task_failed', 'task_timeout', 'retry_exceeded', 'partial_success']) {
      const title = `Task alert: ${event}`
      add(t('ops.events.taskAlert', { event: t(`ops.alertsCenter.taskEvents.${event}`) }), title)
    }
    for (const status of ['failed', 'timeout']) add(t(`ops.task.status.${status}`), `status=${status}`)
  } else {
    for (const status of ['failed', 'timeout']) {
      for (const [label, type] of Object.entries(taskTypes)) {
        add(t(`ops.events.taskTerminal.${status}`, { type: t(`ops.task.taskType.${type}`), name: '' }), `${label} ${status}: `)
      }
      const prefix = `Repository operation ${status}: `
      add(t(`ops.events.repositoryTask.${status}`, { name: '' }), prefix)
      for (const [label, operation] of Object.entries(repositoryOperations)) {
        add(t(`ops.events.repositoryOperations.${operation}`), label)
        add(t(`ops.events.repositoryTask.${status}`, { name: t(`ops.events.repositoryOperations.${operation}`) }), `${prefix}${label}`)
      }
    }
  }
  add(t('ops.events.taskDiagnostic.unsupportedSource', { source: '' }), 'upload error: unsupported source: ')
  return [...aliases].filter(alias => alias.length <= 200 && alias.toLowerCase() !== query)
}

export function taskAlertDisplay(alert: { type?: string; title?: string; message?: string }, t: Translate) {
  const title = alert.title || ''
  const message = alert.message || ''
  if (alert.type !== 'task') return { title, message }
  const event = taskAlertTitlePattern.exec(title)
  return {
    title: event
      ? t('ops.events.taskAlert', { event: t(`ops.alertsCenter.taskEvents.${event[1]}`) })
      : title,
    message: taskDiagnosticDisplay(message, t),
  }
}

export function taskAlertNotificationDisplay(notification: { kind: string; title: string; summary?: string }, t: Translate) {
  const { title } = notification
  const summary = notification.summary || ''
  if (notification.kind !== 'alert') return { title, summary }
  const resolved = title.startsWith('Resolved: ')
  const alertTitle = resolved ? title.slice('Resolved: '.length) : title
  if (!taskAlertTitlePattern.test(alertTitle)) return { title, summary }
  const display = taskAlertDisplay({ type: 'task', title: alertTitle, message: summary }, t)
  return {
    title: resolved ? t('ops.events.resolvedTaskAlert', { title: display.title }) : display.title,
    summary: display.message,
  }
}

/** Localize known historical event text without rewriting evidence or resource names. */
export function operationalEventDisplay(event: OperationalEventText, t: Translate) {
  let title = event.title || ''
  let details = event.details || ''
  if (event.event_type !== 'task.failed' && event.event_type !== 'task.timeout') {
    return { title, details }
  }
  const status = event.event_type === 'task.failed' ? 'failed' : 'timeout'
  for (const [label, type] of Object.entries(taskTypes)) {
    const taskPrefix = `${label} ${status}: `
    if (title.startsWith(taskPrefix)) {
      return {
        title: t(`ops.events.taskTerminal.${status}`, {
          type: t(`ops.task.taskType.${type}`), name: title.slice(taskPrefix.length),
        }),
        details: taskDiagnosticDisplay(details, t),
      }
    }
  }
  const prefix = status === 'failed' ? 'Repository operation failed: ' : 'Repository operation timeout: '
  if (!title.startsWith(prefix)) return { title, details }

  const name = title.slice(prefix.length)
  // Split only the generated action prefix; resource names may themselves contain " · ".
  const separator = name.indexOf(' · ')
  const action = separator < 0 ? name : name.slice(0, separator)
  const operation = repositoryOperations[action]
  const localizedName = operation
    ? t(`ops.events.repositoryOperations.${operation}`) + (separator < 0 ? '' : name.slice(separator))
    : name
  title = t(`ops.events.repositoryTask.${status}`, { name: localizedName })
  if (details === 'Snapshot reconciliation failed; no unconfirmed deletion will be retried.') {
    details = t('ops.events.repositoryTask.reconciliationFailed')
  }
  return { title, details }
}
