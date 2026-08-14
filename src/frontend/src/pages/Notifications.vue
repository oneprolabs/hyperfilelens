<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Bell, CheckCheck, RefreshCw } from 'lucide-vue-next'
import HflPagination from '../components/HflPagination.vue'
import HflTablePanel from '../components/HflTablePanel.vue'
import {
  listUserNotifications,
  markAllUserNotificationsRead,
  markUserNotificationRead,
  type UserNotification,
} from '../lib/notificationApi'
import { formatLocalDateTime } from '../lib/dateTime'

const { t } = useI18n()
const router = useRouter()
const loading = ref(false)
const markingAll = ref(false)
const loadError = ref('')
const notifications = ref<UserNotification[]>([])
const unreadCount = ref(0)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
let requestSequence = 0

const emptyText = computed(() => t('notificationsPage.empty'))

async function loadNotifications() {
  const requestId = ++requestSequence
  loading.value = true
  try {
    const result = await listUserNotifications(pagination.pageSize, pagination.page)
    if (requestId !== requestSequence) return
    notifications.value = result.results
    unreadCount.value = result.unread_count
    pagination.count = result.count
    loadError.value = ''
  } catch {
    if (requestId === requestSequence) {
      loadError.value = t('notificationsPage.loadFailed')
    }
  } finally {
    if (requestId === requestSequence) loading.value = false
  }
}

async function openNotification(notification: UserNotification) {
  if (!notification.is_read) {
    try {
      await markUserNotificationRead(notification.id)
      requestSequence += 1
      loading.value = false
      notification.is_read = true
      unreadCount.value = Math.max(0, unreadCount.value - 1)
      loadError.value = ''
    } catch {
      loadError.value = t('notificationsPage.markReadFailed')
    }
  }
  await router.push(notification.to || '/account/notifications')
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
    loadError.value = t('notificationsPage.markReadFailed')
  } finally {
    markingAll.value = false
  }
}

onMounted(() => void loadNotifications())
watch(
  () => [pagination.page, pagination.pageSize],
  () => void loadNotifications(),
)
</script>

<template>
  <div class="notifications-page">
    <header class="notifications-page__header">
      <p>{{ t('notificationsPage.subtitle') }}</p>
      <el-button
        :disabled="!unreadCount"
        :loading="markingAll"
        @click="markAllRead"
      >
        <CheckCheck :size="16" />
        {{ t('nav.notificationPopover.markAllRead') }}
      </el-button>
    </header>

    <div
      v-if="loadError"
      class="notifications-page__error"
      role="status"
    >
      {{ loadError }}
    </div>

    <HflTablePanel fill>
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
          :data="notifications"
          row-key="id"
          stripe
          class="hfl-list-table"
          :max-height="tableMaxHeight"
        >
          <el-table-column
            width="52"
            fixed="left"
          >
            <template #default="{ row }">
              <span
                class="notifications-page__state"
                :class="{ 'is-unread': !row.is_read }"
              >
                <Bell :size="16" />
              </span>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('notificationsPage.notification')"
            min-width="300"
          >
            <template #default="{ row }">
              <button
                class="notifications-page__link"
                type="button"
                @click="openNotification(row)"
              >
                <strong>{{ row.title }}</strong>
                <span>{{ row.summary || '—' }}</span>
              </button>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('notificationsPage.severity')"
            width="140"
          >
            <template #default="{ row }">
              {{ row.severity }}
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
          <el-table-column
            :label="t('notificationsPage.status')"
            width="120"
          >
            <template #default="{ row }">
              {{ row.is_read ? t('notificationsPage.read') : t('notificationsPage.unread') }}
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

.notifications-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.notifications-page__header p {
  margin: 0;
}

.notifications-page__error {
  padding: 10px 12px;
  border-radius: 6px;
  color: var(--el-color-danger);
  font-size: 13px;
  background: var(--el-color-danger-light-9);
}

.notifications-page__header p {
  color: var(--el-text-color-secondary);
}

.notifications-page__state {
  display: inline-flex;
  color: var(--el-text-color-placeholder);
}

.notifications-page__state.is-unread {
  color: var(--el-color-primary);
}

.notifications-page__link {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.notifications-page__link span {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 767.98px) {
  .notifications-page__header {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
