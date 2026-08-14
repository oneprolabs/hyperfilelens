<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { lifecycleStatusTagAttrs } from '../lib/statusTag'

const props = defineProps<{
  status?: string | null
}>()

const { t } = useI18n()

const label = computed(() => {
  if (!props.status) return '—'
  const labels: Record<string, string> = {
    available: t('protection.backupsPage.snapshotStatusAvailable'),
    partial: t('protection.backupsPage.snapshotStatusPartial'),
    failed: t('protection.backupsPage.snapshotStatusFailed'),
    creating: t('protection.backupsPage.snapshotStatusCreating'),
    deleting: t('protection.backupsPage.snapshotStatusDeleting'),
    delete_failed: t('protection.backupsPage.snapshotStatusDeleteFailed'),
    deleted: t('protection.backupsPage.snapshotStatusDeleted'),
  }
  return labels[props.status] || props.status
})
</script>

<template>
  <ElTag
    v-bind="lifecycleStatusTagAttrs(status)"
    size="small"
  >
    {{ label }}
  </ElTag>
</template>
