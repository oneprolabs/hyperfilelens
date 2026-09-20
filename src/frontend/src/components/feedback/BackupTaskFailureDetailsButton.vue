<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { TaskRow } from '../../lib/taskApi'
import { buildTaskFailureErrorDetails, hasFailureDetails } from '../../lib/backupTaskFailureLogic'
import { openErrorDetails } from '../../lib/errors/details'

const props = defineProps<{ task: TaskRow; currentTaskUuid?: string }>()
const { t } = useI18n()

function openDetails() {
  openErrorDetails(buildTaskFailureErrorDetails({ task: props.task, t }), { currentTaskUuid: props.currentTaskUuid })
}
</script>

<template>
  <ElButton
    v-if="task.task_type === 'backup' && (['failed', 'timeout', 'partial'].includes(task.status) || (task.status === 'success' && (task.error_details?.severity === 'warning' || hasFailureDetails(task))))"
    @click="openDetails"
  >
    {{ t('feedback.toast.viewDetails') }}
  </ElButton>
</template>
