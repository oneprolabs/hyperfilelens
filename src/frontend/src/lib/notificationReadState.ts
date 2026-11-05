import { currentUser } from '../composables/useAuth'
import { readStoredOrganizationKey } from './organizationStorage'

export type NotificationScope = {
  userId: number | null
  organizationKey: string
}

/** Capture the account and organization used when a notification request starts. */
export function notificationScope(): NotificationScope {
  return {
    userId: currentUser.value?.id ?? null,
    organizationKey: readStoredOrganizationKey() || currentUser.value?.access_profile?.org_key || '',
  }
}

export function sameNotificationScope(left: NotificationScope, right: NotificationScope): boolean {
  return left.userId === right.userId && left.organizationKey === right.organizationKey
}

/** In-memory read-state updates are valid only within the initiating scope. */
export type NotificationReadChange = { scope: NotificationScope } & ({ id: string } | { all: true })

const listeners = new Set<(change: NotificationReadChange) => void>()

export function onNotificationReadChange(listener: (change: NotificationReadChange) => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function publishNotificationReadChange(change: NotificationReadChange) {
  for (const listener of listeners) listener(change)
}
