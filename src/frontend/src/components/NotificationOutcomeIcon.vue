<script setup lang="ts">
import { computed } from 'vue'
import { Check, CircleAlert, Info, X } from 'lucide-vue-next'
import type { UserNotification } from '../lib/notificationApi'
import { notificationOutcome } from '../lib/notificationDisplay'

const props = defineProps<{ notification: UserNotification }>()
const outcome = computed(() => notificationOutcome(props.notification))
const icons = { approved: Check, rejected: X, failed: CircleAlert, cancelled: X, error: CircleAlert, warning: CircleAlert, info: Info }
</script>

<template>
  <span
    class="notification-outcome-icon"
    :class="`notification-outcome-icon--${outcome}`"
    aria-hidden="true"
  >
    <component
      :is="icons[outcome]"
      :size="16"
    />
  </span>
</template>

<style scoped>
.notification-outcome-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.notification-outcome-icon--failed,
.notification-outcome-icon--error {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.notification-outcome-icon--warning,
.notification-outcome-icon--rejected {
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
}

.notification-outcome-icon--approved {
  color: var(--el-color-success-dark-2);
  background: var(--el-color-success-light-9);
}
</style>
