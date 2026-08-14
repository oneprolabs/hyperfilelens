<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{
  saving?: boolean
  saveDisabled?: boolean
  cancelDisabled?: boolean
  saveLabel?: string
}>()

defineEmits<{
  cancel: []
  save: []
}>()

const { t } = useI18n()
</script>

<template>
  <div class="el-drawer__footer-actions">
    <ElButton
      :disabled="cancelDisabled || saving"
      @click="$emit('cancel')"
    >
      {{ t('repositoriesPage.btnCancel') }}
    </ElButton>
    <ElButton
      type="primary"
      :loading="saving"
      :disabled="saveDisabled || saving"
      @click="$emit('save')"
    >
      {{ saveLabel || t('repositoriesPage.btnSaveChanges') }}
    </ElButton>
  </div>
</template>
