<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronRight } from 'lucide-vue-next'

const props = defineProps<{
  label: string
  clickable?: boolean
  loading?: boolean
  failure?: boolean
}>()
const emit = defineEmits<{ (e: 'click', event: MouseEvent): void }>()
const { t } = useI18n()
const action = computed(() => t(props.failure
  ? 'protection.backupsPage.statusViewDetails'
  : 'protection.backupsPage.statusViewTask'))

function activate(event: MouseEvent) {
  if (props.clickable && !props.loading) emit('click', event)
}
</script>

<template>
  <div class="status-detail-cell">
    <slot />
    <button
      v-if="clickable"
      type="button"
      class="status-detail-cell__action"
      :disabled="loading"
      :aria-busy="loading || undefined"
      :aria-label="t('protection.backupsPage.statusDetailActionLabel', { status: label, action })"
      @click.stop="activate"
    >
      <ChevronRight
        v-if="!loading"
        :size="12"
        class="status-detail-cell__arrow"
        aria-hidden="true"
      />
      <span>{{ loading ? t('common.loading') : action }}</span>
    </button>
  </div>
</template>

<style scoped>
.status-detail-cell {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  text-align: left;
  gap: 4px;
  max-width: 100%;
  vertical-align: middle;
}

.status-detail-cell__action {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-height: 20px;
  padding: 0;
  border: 0;
  border-radius: var(--el-border-radius-small, 4px);
  background: transparent;
  color: var(--el-color-primary);
  font-family: inherit;
  font-size: 12px;
  line-height: 20px;
  white-space: nowrap;
  cursor: pointer;
}

.status-detail-cell__arrow {
  flex-shrink: 0;
}

.status-detail-cell__action:hover:not(:disabled) {
  text-decoration: underline;
  text-underline-offset: 2px;
}

.status-detail-cell__action:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.status-detail-cell__action:disabled {
  color: var(--el-text-color-secondary);
  cursor: progress;
}
</style>
