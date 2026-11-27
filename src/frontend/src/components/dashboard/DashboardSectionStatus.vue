<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { DashboardSectionState } from '../../lib/dashboardApi'

defineProps<{ state: DashboardSectionState }>()
defineEmits<{ retry: [] }>()
const { t } = useI18n()
</script>

<template>
  <div class="dashboard-section-status" role="status">
    <span>
      {{ t(state === 'loading' ? 'common.loading' : state === 'forbidden' ? 'errors.codes.authForbidden' : 'errors.generic.loadFailed') }}
    </span>
    <ElButton v-if="state === 'unavailable'" text size="small" @click="$emit('retry')">
      {{ t('common.retry') }}
    </ElButton>
  </div>
</template>

<style scoped>
.dashboard-section-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: 2rem;
  padding-inline: 0.75rem;
  font-size: 0.875rem;
}
</style>
