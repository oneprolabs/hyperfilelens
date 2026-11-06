<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Bell, LoaderCircle, MailCheck } from 'lucide-vue-next'
import NotificationOutcomeIcon from './NotificationOutcomeIcon.vue'
import { notificationDisplay } from '../lib/notificationDisplay'
import { notificationScope, sameNotificationScope, onNotificationReadChange, publishNotificationReadChange } from '../lib/notificationReadState'
import { currentUser } from '../composables/useAuth'
import {
  listUserNotifications,
  markAllUserNotificationsRead,
  markUserNotificationRead,
  type UserNotification,
} from '../lib/notificationApi'

const { t } = useI18n()
const router = useRouter()
const visible = ref(false)
const loading = ref(false)
const markingAll = ref(false)
const loadError = ref('')
const notifications = ref<UserNotification[]>([])
const unreadCount = ref(0)

const POLL_MS = 30000
const PAGE_SIZE = 10

let cancelled = false
let interval: ReturnType<typeof setInterval> | undefined
let requestSequence = 0
let displayedScope = notificationScope()

const badgeCount = computed(() => unreadCount.value)

const badgeText = computed(() => (badgeCount.value > 99 ? '99+' : String(badgeCount.value)))

const popperOptions = {
  modifiers: [
    {
      name: 'preventOverflow',
      options: {
        boundary: 'viewport',
        padding: 12,
      },
    },
    {
      name: 'flip',
      options: {
        fallbackPlacements: ['bottom-start', 'top-end', 'top-start'],
      },
    },
  ],
}

const pendingReads = new Set<string>()
const displayNotifications = computed(() => notifications.value.map(notification => ({
  ...notification,
  display: notificationDisplay(notification, t),
})))

const stopReadSync = onNotificationReadChange((change) => {
  if (!sameNotificationScope(change.scope, notificationScope())) return
  if (!sameNotificationScope(displayedScope, change.scope)) {
    void loadNotifications()
    return
  }
  const wasLoading = loading.value
  requestSequence += 1
  loading.value = false
  if ('all' in change) {
    notifications.value = notifications.value.map(item => ({ ...item, is_read: true }))
    unreadCount.value = 0
  } else {
    const item = notifications.value.find(item => item.id === change.id)
    if (!item) { void loadNotifications(); return }
    if (!item.is_read) unreadCount.value = Math.max(0, unreadCount.value - 1)
    item.is_read = true
  }
  // Read-state invalidation must not silently discard an in-flight refresh.
  if (wasLoading) void loadNotifications()
})

function formatRelativeTime(iso?: string) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'

  const diffSec = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000))
  if (diffSec < 60) return t('nav.notificationPopover.relative.justNow')

  const minutes = Math.floor(diffSec / 60)
  if (minutes < 60) return t('nav.notificationPopover.relative.minutesAgo', { n: minutes })

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('nav.notificationPopover.relative.hoursAgo', { n: hours })

  const days = Math.floor(hours / 24)
  if (days === 1) return t('nav.notificationPopover.relative.yesterday')

  return t('nav.notificationPopover.relative.daysAgo', { n: days })
}

async function loadNotifications() {
  const scope = notificationScope()
  if (scope.userId === null) return
  const requestId = ++requestSequence
  loading.value = true
  try {
    const res = await listUserNotifications(PAGE_SIZE, 1, scope.organizationKey)
    if (cancelled || requestId !== requestSequence || !sameNotificationScope(scope, notificationScope())) return
    displayedScope = scope
    notifications.value = res.results
    unreadCount.value = res.unread_count
    loadError.value = ''
  } catch {
    if (!cancelled && requestId === requestSequence && sameNotificationScope(scope, notificationScope())) {
      loadError.value = t('nav.notificationPopover.loadFailed')
    }
  } finally {
    if (!cancelled && requestId === requestSequence) loading.value = false
  }
}

async function onItemClick(notification: UserNotification) {
  const scope = notificationScope()
  if (scope.userId === null) return
  if (!sameNotificationScope(displayedScope, scope)) { void loadNotifications(); return }
  if (pendingReads.has(notification.id)) return
  visible.value = false
  if (!notification.is_read) {
    pendingReads.add(notification.id)
    try {
      await markUserNotificationRead(notification.id, scope.organizationKey)
      if (cancelled || !sameNotificationScope(scope, notificationScope())) return
      publishNotificationReadChange({ scope, id: notification.id })
      loadError.value = ''
    } catch {
      if (!cancelled && sameNotificationScope(scope, notificationScope())) {
        loadError.value = t('nav.notificationPopover.markReadFailed')
      }
    } finally {
      pendingReads.delete(notification.id)
    }
  }
  if (!cancelled && sameNotificationScope(scope, notificationScope())) {
    void router.push(notification.to || '/account/notifications')
  }
}

async function markAllRead() {
  if (!unreadCount.value || markingAll.value) return
  const scope = notificationScope()
  if (scope.userId === null) return
  if (!sameNotificationScope(displayedScope, scope)) { void loadNotifications(); return }
  markingAll.value = true
  try {
    await markAllUserNotificationsRead(scope.organizationKey)
    if (cancelled || !sameNotificationScope(scope, notificationScope())) return
    publishNotificationReadChange({ scope, all: true })
    loadError.value = ''
  } catch {
    if (!cancelled && sameNotificationScope(scope, notificationScope())) {
      loadError.value = t('nav.notificationPopover.markReadFailed')
    }
  } finally {
    markingAll.value = false
  }
}

function viewAll() {
  visible.value = false
  void router.push('/account/notifications')
}

watch(visible, (open) => {
  if (open) void loadNotifications()
})

watch(
  [() => currentUser.value?.id, () => currentUser.value?.access_profile?.org_key],
  () => {
    requestSequence += 1
    displayedScope = notificationScope()
    notifications.value = []
    unreadCount.value = 0
    loading.value = false
    loadError.value = ''
    visible.value = false
    void loadNotifications()
  },
)

onMounted(() => {
  void loadNotifications()
  interval = setInterval(() => void loadNotifications(), POLL_MS)
})

onUnmounted(() => {
  cancelled = true
  stopReadSync()
  if (interval) clearInterval(interval)
})
</script>

<template>
  <div class="nav-notification-wrap">
    <HflPopover
      v-model:visible="visible"
      trigger="click"
      placement="bottom-end"
      :width="420"
      :show-arrow="false"
      effect="light"
      popper-class="nav-dropdown-popover nn-popover"
      :popper-options="popperOptions"
      :offset="8"
    >
      <template #reference>
        <button
          type="button"
          class="nav-notification-trigger"
          :aria-label="t('nav.notificationPopover.bellAria')"
        >
          <Bell :size="18" />
          <span
            v-if="badgeCount > 0"
            class="nav-notification-badge"
          >{{ badgeText }}</span>
        </button>
      </template>

      <div class="nav-dropdown-panel">
        <header class="nav-dropdown-panel__head nn-head">
          <h3 class="nav-dropdown-panel__title">
            {{ t('nav.notificationPopover.title') }}
          </h3>
          <button
            v-if="unreadCount > 0"
            type="button"
            class="nav-dropdown-panel__foot-link nav-dropdown-panel__head-action nn-mark-all"
            :aria-busy="markingAll"
            :disabled="markingAll"
            @click="markAllRead"
          >
            <LoaderCircle
              v-if="markingAll"
              class="nn-mark-all__loading"
              :size="16"
              aria-hidden="true"
            />
            <MailCheck
              v-else
              :size="16"
              aria-hidden="true"
            />
            {{ t('nav.notificationPopover.markAllRead') }}
          </button>
        </header>

        <div class="nav-dropdown-panel__body nav-dropdown-panel__body--flush">
          <div
            v-if="loadError"
            class="nn-load-error"
            role="status"
          >
            {{ loadError }}
          </div>
          <div
            v-if="loading && !notifications.length"
            class="nav-dropdown-panel__empty"
          >
            {{ t('nav.notificationPopover.loading') }}
          </div>
          <div
            v-else-if="!notifications.length && !loadError"
            class="nav-dropdown-panel__empty"
          >
            {{ t('nav.notificationPopover.empty') }}
          </div>
          <ul
            v-else
            class="nav-dropdown-panel__list nn-list"
            role="list"
          >
            <li
              v-for="notification in displayNotifications"
              :key="notification.id"
              class="nn-item"
              :class="{ 'is-unread': !notification.is_read }"
              role="button"
              tabindex="0"
              :aria-label="`${notification.display.title} · ${t(notification.is_read ? 'notificationsPage.read' : 'notificationsPage.unread')}`"
              @click="onItemClick(notification)"
              @keydown.enter.prevent="onItemClick(notification)"
              @keydown.space.prevent="onItemClick(notification)"
            >
              <NotificationOutcomeIcon :notification="notification" />
              <div class="nn-item-body">
                <div class="nn-item-title">
                  {{ notification.display.title }}
                </div>
                <div
                  v-if="notification.display.summary"
                  class="nn-item-summary"
                >
                  {{ notification.display.summary }}
                </div>
                <div class="nn-item-time">
                  {{ formatRelativeTime(notification.occurred_at || notification.updated_at) }}
                </div>
              </div>
              <span
                v-if="!notification.is_read"
                class="nn-unread-dot"
                aria-hidden="true"
              />
            </li>
          </ul>
        </div>

        <footer class="nav-dropdown-panel__foot">
          <button
            type="button"
            class="nav-dropdown-panel__foot-link"
            @click="viewAll"
          >
            <span>{{ t('nav.notificationPopover.viewAll') }}</span>
            <ArrowRight
              :size="14"
              aria-hidden="true"
            />
          </button>
        </footer>
      </div>
    </HflPopover>
  </div>
</template>

<style scoped>
.nav-notification-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.nav-notification-trigger {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--nav-notification-color, rgba(255, 255, 255, 0.8));
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.nav-notification-trigger:hover,
.nav-notification-trigger:focus-visible {
  background: var(--icon-btn-hover-bg, rgba(255, 255, 255, 0.08));
  color: var(--nav-notification-hover-color, #fff);
}

.nav-notification-trigger:focus-visible {
  outline: 2px solid var(--color-primary, #6D5EF6);
  outline-offset: 2px;
}

@media (min-width: 1024px) and (max-width: 1439.98px) {
  .nav-notification-trigger {
    color: var(--icon-btn-color, #aeb2c5);
  }

  .nav-notification-trigger:hover,
  .nav-notification-trigger:focus-visible {
    color: var(--icon-btn-hover-color, #E2E2E2);
  }
}

@media (max-width: 1023.98px) {
  .nav-notification-trigger {
    width: 44px;
    height: 44px;
  }
}

.nav-notification-badge {
  position: absolute;
  top: -4px;
  right: -2px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  color: #fff;
  text-align: center;
  background-color: var(--color-error);
  border-radius: 9px;
}

.nn-load-error {
  padding: 10px 14px;
  color: var(--el-color-danger);
  font-size: 12px;
  background: var(--el-color-danger-light-9);
}

.nn-list {
  padding: 0;
  max-height: min(440px, 60vh);
  overscroll-behavior: contain;
}

.nn-head {
  align-items: center;
  padding: 12px 16px;
  flex-wrap: wrap;
}

.nn-mark-all {
  display: inline-flex;
  width: auto;
  height: 30px;
  gap: 6px;
  margin: 0;
  padding: 0 8px;
  font-weight: 400;
}

.nn-mark-all:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  background: var(--color-primary-light);
  border-color: var(--color-primary-border);
}

.nn-mark-all:disabled {
  cursor: wait;
  opacity: 0.65;
}

.nn-mark-all__loading {
  animation: nn-mark-all-spin 1s linear infinite;
}

@keyframes nn-mark-all-spin {
  to { transform: rotate(360deg); }
}

@media (pointer: coarse) {
  .nn-mark-all {
    min-height: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .nn-mark-all {
    transition: none;
  }

  .nn-mark-all__loading {
    animation: none;
  }
}

.nn-mark-all :deep(svg) {
  flex-shrink: 0;
}

.nn-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.nn-item:hover {
  background-color: var(--el-fill-color-light);
}

.nn-item:last-child {
  border-bottom: none;
}

.nn-item.is-unread {
  background-color: color-mix(in srgb, var(--el-color-primary) 5%, transparent);
}

.nn-item.is-unread:hover {
  background-color: color-mix(in srgb, var(--el-color-primary) 9%, transparent);
}

.nn-item:focus-visible {
  outline: 2px solid var(--color-primary, #457ab0);
  outline-offset: -2px;
}

.nn-item-body {
  flex: 1;
  min-width: 0;
}

.nn-item-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  overflow-wrap: anywhere;
  color: var(--el-text-color-primary);
}

.nn-item-summary {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-regular);
}

.nn-item-time {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.nn-unread-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  margin-top: 6px;
  background-color: var(--color-primary, #457ab0);
  border-radius: 50%;
}
</style>

<style>
.nn-popover.el-popper {
  box-shadow: var(--el-box-shadow-light);
}
</style>
