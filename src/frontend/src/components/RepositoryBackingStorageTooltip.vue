<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import HflPopover from './HflPopover.vue'

defineProps<{
  rows: Array<{ label: string; value: string }>
  note: string
}>()

const { t } = useI18n()
</script>

<template>
  <HflPopover
    trigger="hover"
    placement="bottom-start"
    :width="360"
    :fallback-placements="['top-start', 'bottom-end']"
    popper-class="repository-info-popper"
  >
    <template #reference>
      <slot />
    </template>
    <div class="repository-info-popover">
      <div class="repository-info-popover__head">
        <div class="repository-info-popover__title">
          {{ t('repositoriesPage.detailFieldBackingStorage') }}
        </div>
      </div>
      <dl class="repository-info-popover__rows">
        <div
          v-for="row in rows"
          :key="row.label"
          class="repository-info-popover__row"
        >
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </div>
      </dl>
      <div class="repository-info-popover__note">
        {{ note }}
      </div>
    </div>
  </HflPopover>
</template>
