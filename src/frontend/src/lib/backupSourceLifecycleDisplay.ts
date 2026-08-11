export const BACKUP_SOURCE_REGISTERED_LABEL_KEY = 'protection.sourceResources.lifecycleRegistered'

type LifecycleDisplay = {
  labelKey: string
}

/** Use backup-source terminology without changing the persisted lifecycle enum. */
export function backupSourceLifecycleDisplay<T extends LifecycleDisplay>(display: T): T {
  if (display.labelKey !== 'nodeLifecycle.state.active') return display
  return {
    ...display,
    labelKey: BACKUP_SOURCE_REGISTERED_LABEL_KEY,
  }
}
