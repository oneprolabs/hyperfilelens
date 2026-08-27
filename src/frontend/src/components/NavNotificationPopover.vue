<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowUpRight, Bell } from 'lucide-vue-next'
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

type Severity = 'critical' | 'warning' | 'insight' | 'info'

const POLL_MS = 30000
const PAGE_SIZE = 10

let cancelled = false
let interval: ReturnType<typeof setInterval> | undefined
let requestSequence = 0

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

function severityClass(notification: UserNotification): Severity {
  if (notification.severity === 'critical') return 'critical'
  if (notification.severity === 'warning') return 'warning'
  if (notification.severity === 'info') return 'info'
  return 'info'
}

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
  const requestId = ++requestSequence
  loading.value = true
  try {
    const res = await listUserNotifications(PAGE_SIZE)
    if (cancelled || requestId !== requestSequence) return
    notifications.value = res.results
    unreadCount.value = res.unread_count
    loadError.value = ''
  } catch {
    if (!cancelled && requestId === requestSequence) {
      loadError.value = t('nav.notificationPopover.loadFailed')
    }
  } finally {
    if (!cancelled && requestId === requestSequence) loading.value = false
  }
}

async function onItemClick(notification: UserNotification) {
  visible.value = false
  if (!notification.is_read) {
    try {
      await markUserNotificationRead(notification.id)
      requestSequence += 1
      loading.value = false
      notification.is_read = true
      unreadCount.value = Math.max(0, unreadCount.value - 1)
      loadError.value = ''
    } catch {
      loadError.value = t('nav.notificationPopover.markReadFailed')
    }
  }
  void router.push(notification.to || '/account/notifications')
}

async function markAllRead() {
  if (!unreadCount.value || markingAll.value) return

  markingAll.value = true
  try {
    await markAllUserNotificationsRead()
    requestSequence += 1
    loading.value = false
    notifications.value = notifications.value.map((notification) => ({
      ...notification,
      is_read: true,
    }))
    unreadCount.value = 0
    loadError.value = ''
  } catch {
    loadError.value = t('nav.notificationPopover.markReadFailed')
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

onMounted(() => {
  void loadNotifications()
  interval = setInterval(() => void loadNotifications(), POLL_MS)
})

onUnmounted(() => {
  cancelled = true
  if (interval) clearInterval(interval)
})
</script>

<template>
  <div class="nav-notification-wrap">
    <HflPopover
      v-model:visible="visible"
      trigger="click"
      placement="bottom-end"
      :width="380"
      :show-arrow="false"
      effect="light"
      popper-class="nav-dropdown-popover"
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
        <header class="nav-dropdown-panel__head">
          <h3 class="nav-dropdown-panel__title">
            {{ t('nav.notificationPopover.title') }} ({{ badgeCount }})
          </h3>
          <ElButton
            v-if="unreadCount > 0"
            text
            type="primary"
            size="small"
            class="nav-dropdown-panel__head-action"
            :loading="markingAll"
            @click="markAllRead"
          >
            {{ t('nav.notificationPopover.markAllRead') }}
          </ElButton>
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
              v-for="notification in notifications"
              :key="notification.id"
              class="nn-item"
              role="button"
              tabindex="0"
              @click="onItemClick(notification)"
              @keydown.enter.prevent="onItemClick(notification)"
              @keydown.space.prevent="onItemClick(notification)"
            >
              <span
                class="nn-icon"
                :class="`nn-icon--${severityClass(notification)}`"
                aria-hidden="true"
              />
              <div class="nn-item-body">
                <div class="nn-item-title">
                  {{ notification.title }}
                </div>
                <div class="nn-item-meta">
                  <span class="nn-item-summary">{{ notification.summary || '—' }}</span>
                  <span class="nn-item-sep">|</span>
                  <span class="nn-item-time">{{ formatRelativeTime(notification.occurred_at || notification.updated_at) }}</span>
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
            <ArrowUpRight
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
  padding: 4px 0;
}

.nn-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 16px;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.nn-item:hover {
  background-color: rgba(69, 125, 176, 0.08);
}

.nn-item:focus-visible {
  outline: 2px solid var(--color-primary, #457ab0);
  outline-offset: -2px;
}

.nn-icon {
  flex-shrink: 0;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 50%;
}

.nn-icon--critical {
  background-color: var(--color-error);
}

.nn-icon--warning {
  background-color: var(--color-warning);
}

.nn-icon--insight {
  background-color: var(--color-primary, #457ab0);
}

.nn-icon--info {
  background-color: var(--color-grey-5, #bfbfbf);
}

.nn-item-body {
  flex: 1;
  min-width: 0;
}

.nn-item-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--color-text-title, #303133);
}

.nn-item-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-tertiary, #909399);
}

.nn-item-sep {
  color: var(--color-border, #d9d9d9);
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
