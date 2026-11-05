<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { MailCheck, RefreshCw } from 'lucide-vue-next'
import NotificationOutcomeIcon from '../components/NotificationOutcomeIcon.vue'
import HflPagination from '../components/HflPagination.vue'
import HflTablePanel from '../components/HflTablePanel.vue'
import {
  listUserNotifications,
  markAllUserNotificationsRead,
  markUserNotificationRead,
  type UserNotification,
} from '../lib/notificationApi'
import { formatLocalDateTime } from '../lib/dateTime'
import { notificationDisplay } from '../lib/notificationDisplay'
import { notificationScope, sameNotificationScope, onNotificationReadChange, publishNotificationReadChange } from '../lib/notificationReadState'
import { currentUser } from '../composables/useAuth'

const { t } = useI18n()
const router = useRouter()
const loading = ref(false)
const markingAll = ref(false)
const loadError = ref('')
const notifications = ref<UserNotification[]>([])
const unreadCount = ref(0)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
let requestSequence = 0
let displayedScope = notificationScope()
let cancelled = false
const pendingReads = new Set<string>()

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
  // A read event may arrive while the user is loading a different page.
  if (wasLoading) void loadNotifications()
})

const emptyText = computed(() => t('notificationsPage.empty'))
const displayNotifications = computed(() => notifications.value.map(notification => ({
  ...notification,
  display: notificationDisplay(notification, t),
})))

function notificationRowClass({ row }: { row: UserNotification }) {
  return row.is_read ? '' : 'notifications-page__row--unread'
}

async function loadNotifications() {
  const scope = notificationScope()
  if (scope.userId === null) return
  const requestId = ++requestSequence
  loading.value = true
  try {
    const result = await listUserNotifications(pagination.pageSize, pagination.page, scope.organizationKey)
    if (cancelled || requestId !== requestSequence || !sameNotificationScope(scope, notificationScope())) return
    displayedScope = scope
    notifications.value = result.results
    unreadCount.value = result.unread_count
    pagination.count = result.count
    loadError.value = ''
  } catch {
    if (!cancelled && requestId === requestSequence && sameNotificationScope(scope, notificationScope())) {
      loadError.value = t('notificationsPage.loadFailed')
    }
  } finally {
    if (!cancelled && requestId === requestSequence) loading.value = false
  }
}

async function openNotification(notification: UserNotification) {
  const scope = notificationScope()
  if (scope.userId === null) return
  if (!sameNotificationScope(displayedScope, scope)) { void loadNotifications(); return }
  if (pendingReads.has(notification.id)) return
  if (!notification.is_read) {
    pendingReads.add(notification.id)
    try {
      await markUserNotificationRead(notification.id, scope.organizationKey)
      if (cancelled || !sameNotificationScope(scope, notificationScope())) return
      publishNotificationReadChange({ scope, id: notification.id })
      loadError.value = ''
    } catch {
      if (!cancelled && sameNotificationScope(scope, notificationScope())) {
        loadError.value = t('notificationsPage.markReadFailed')
      }
    } finally {
      pendingReads.delete(notification.id)
    }
  }
  if (!cancelled && sameNotificationScope(scope, notificationScope())) {
    await router.push(notification.to || '/account/notifications')
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
      loadError.value = t('notificationsPage.markReadFailed')
    }
  } finally {
    markingAll.value = false
  }
}

onMounted(() => void loadNotifications())
onUnmounted(() => { cancelled = true; stopReadSync() })
watch(
  [() => currentUser.value?.id, () => currentUser.value?.access_profile?.org_key],
  () => {
    requestSequence += 1
    displayedScope = notificationScope()
    notifications.value = []
    unreadCount.value = 0
    loading.value = false
    pagination.count = 0
    loadError.value = ''
    if (pagination.page !== 1) pagination.page = 1
    else void loadNotifications()
  },
)
watch(
  () => [pagination.page, pagination.pageSize],
  () => void loadNotifications(),
)
</script>

<template>
  <div class="notifications-page">
    <div
      v-if="loadError"
      class="notifications-page__error"
      role="status"
    >
      {{ loadError }}
    </div>

    <HflTablePanel fill>
      <template #toolbar>
        <el-button
          type="primary"
          :disabled="!unreadCount"
          :loading="markingAll"
          @click="markAllRead"
        >
          <MailCheck
            :size="16"
            aria-hidden="true"
          />
          {{ t('nav.notificationPopover.markAllRead') }}
        </el-button>
      </template>
      <template #toolbar-utility>
        <el-button
          class="hfl-refresh-button"
          :disabled="loading"
          :title="t('ops.task.btnRefresh')"
          :aria-label="t('ops.task.btnRefresh')"
          @click="loadNotifications"
        >
          <RefreshCw
            :size="16"
            :class="{ 'is-spinning': loading }"
          />
        </el-button>
      </template>
      <template #table="{ tableMaxHeight }">
        <el-table
          v-loading="loading"
          :data="displayNotifications"
          row-key="id"
          :row-class-name="notificationRowClass"
          stripe
          class="hfl-list-table"
          :max-height="tableMaxHeight"
        >
          <el-table-column
            :label="t('notificationsPage.notification')"
            min-width="300"
          >
            <template #default="{ row }">
              <button
                class="notifications-page__link"
                :class="{ 'is-unread': !row.is_read }"
                type="button"
                @click="openNotification(row)"
              >
                <NotificationOutcomeIcon :notification="row" />
                <span class="notifications-page__content">
                  <span class="notifications-page__subject">{{ row.display.title }}</span>
                  <span
                    v-if="row.display.summary"
                    class="notifications-page__summary"
                  >{{ row.display.summary }}</span>
                </span>
              </button>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('notificationsPage.status')"
            width="140"
          >
            <template #default="{ row }">
              <span
                class="notifications-page__read-status"
                :class="{ 'is-unread': !row.is_read }"
              >{{ row.is_read ? t('notificationsPage.read') : t('notificationsPage.unread') }}</span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('notificationsPage.received')"
            width="190"
          >
            <template #default="{ row }">
              {{ formatLocalDateTime(row.occurred_at || row.updated_at, '—') }}
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              v-if="!loading"
              :description="emptyText"
              :image-size="72"
            />
          </template>
        </el-table>
      </template>
      <template #footer>
        <HflPagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          layout="total, sizes, prev, pager, next"
          :total="pagination.count"
          class="hfl-list-footer__pagination"
        />
      </template>
    </HflTablePanel>
  </div>
</template>

<style scoped>
.notifications-page {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  flex-direction: column;
  gap: 16px;
}

.notifications-page__error {
  padding: 10px 12px;
  border-radius: 6px;
  color: var(--el-color-danger);
  font-size: 13px;
  background: var(--el-color-danger-light-9);
}

.notifications-page__read-status {
  color: var(--el-text-color-secondary);
}

.notifications-page__read-status.is-unread {
  color: var(--el-color-primary);
}

.notifications-page :deep(.el-table__body tr.notifications-page__row--unread > td.el-table__cell) {
  background: color-mix(in srgb, var(--el-color-primary) 5%, var(--el-bg-color));
}

.notifications-page :deep(.el-table__body tr.notifications-page__row--unread:hover > td.el-table__cell) {
  background: color-mix(in srgb, var(--el-color-primary) 9%, var(--el-bg-color));
}

.notifications-page__link {
  display: flex;
  align-items: flex-start;
  width: 100%;
  min-width: 0;
  gap: 12px;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.notifications-page__link:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  border-radius: 4px;
}

.notifications-page__content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  overflow-wrap: anywhere;
  white-space: normal;
}

.notifications-page__subject {
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 500;
  line-height: 1.5;
}

.notifications-page__link.is-unread .notifications-page__subject {
  font-weight: 600;
}

.notifications-page__summary {
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.5;
}
</style>
