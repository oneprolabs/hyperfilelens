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

/** Localize known historical event text without rewriting evidence or resource names. */
export function operationalEventDisplay(event: OperationalEventText, t: Translate) {
  let title = event.title || ''
  let details = event.details || ''
  if (event.event_type !== 'task.failed' && event.event_type !== 'task.timeout') {
    return { title, details }
  }
  const status = event.event_type === 'task.failed' ? 'failed' : 'timeout'
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
