<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { safeErrorDetailText } from '../../lib/errors/details'

const props = defineProps<{ detail: unknown }>()
const { t } = useI18n()
const text = computed(() => safeErrorDetailText(props.detail))
</script>

<template>
  <details
    v-if="text"
    class="hfl-error-technical-details mt-3 text-xs"
  >
    <summary class="cursor-pointer text-[var(--el-text-color-secondary)]">
      {{ t('ops.task.failureDetails.technicalDetails') }}
    </summary>
    <pre class="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--el-border-color)] bg-[var(--el-fill-color-light)] p-3">{{ text }}</pre>
  </details>
</template>
