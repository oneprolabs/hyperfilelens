<script setup lang="ts">
import { ElCheckbox } from 'element-plus'
import './backupSourceFlowActionDialog.css'

defineProps<{ label: string; hint: string; disabled?: boolean }>()
const selected = defineModel<boolean>({ default: false })
</script>

<template>
  <div class="hfl-flow-action-dialog__force-panel">
    <label class="hfl-flow-action-dialog__force">
      <ElCheckbox
        v-model="selected"
        :disabled="disabled"
      />
      <span class="hfl-flow-action-dialog__force-copy">
        <span class="hfl-flow-action-dialog__force-label">{{ label }}</span>
        <span class="hfl-flow-action-dialog__force-hint">{{ hint }}</span>
      </span>
    </label>
  </div>
</template>

<style scoped>
.hfl-flow-action-dialog__force {
  font-size: 14px;
  line-height: 22px;
}
</style>
