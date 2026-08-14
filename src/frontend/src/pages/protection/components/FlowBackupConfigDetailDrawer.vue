<script setup lang="ts">
import { computed } from 'vue'
import { useProtectionDemoStore } from '../../../composables/useProtectionDemoStore'
import { useResponsiveDrawerWidth } from '../../../composables/useResponsiveDrawerWidth'
import BackupConfigDetailPanel from './BackupConfigDetailPanel.vue'

const props = defineProps<{
  modelValue: boolean
  backupId: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const store = useProtectionDemoStore()
const { drawerSize } = useResponsiveDrawerWidth()

const drawerOpen = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const backupTitle = computed(() => {
  if (!props.backupId) return ''
  return store.getBackup(props.backupId)?.name ?? props.backupId
})

</script>

<template>
  <el-drawer
    v-model="drawerOpen"
    direction="rtl"
    :size="drawerSize"
    destroy-on-close
    append-to-body
    :z-index="3100"
    class="dp-flow-backup-config-detail-drawer"
    :show-close="true"
  >
    <template
      v-if="backupId"
      #header
    >
      <span class="truncate text-base font-semibold text-slate-900">{{ backupTitle }}</span>
    </template>
    <BackupConfigDetailPanel
      v-if="backupId && drawerOpen"
      :backup-id="backupId"
    />
  </el-drawer>
</template>

<style scoped>
.dp-flow-backup-config-detail-drawer :deep(.el-drawer__body) {
  padding-top: 8px;
}

@media (max-width: 768px) {
  .dp-flow-backup-config-detail-drawer.el-drawer {
    width: 92% !important;
  }
}
</style>
