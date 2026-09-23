<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { RuntimeStatusRow } from './runtimeStatus'

defineProps<{
  rows: RuntimeStatusRow[]
  hideHeader?: boolean
}>()

const { t } = useI18n()
</script>

<template>
  <div
    class="runtime-status-table"
  >
    <div
      v-if="!hideHeader"
      class="runtime-status-table__head"
    >
      <span>{{ t('platformOps.settings.environment.serviceColumn') }}</span>
      <span>{{ t('platformOps.settings.environment.runtimeStateColumn') }}</span>
      <span>{{ t('platformOps.settings.environment.healthCheckColumn') }}</span>
      <span>{{ t('platformOps.settings.environment.businessAvailabilityColumn') }}</span>
      <span>{{ t('platformOps.settings.environment.detailsColumn') }}</span>
    </div>
    <div
      v-for="row in rows"
      :key="row.key"
      class="runtime-status-table__row"
    >
      <span class="runtime-status-table__service">{{ row.service }}</span>
      <el-tag
        size="small"
        :type="row.runtime.type"
        effect="plain"
      >
        {{ row.runtime.label }}
      </el-tag>
      <el-tag
        size="small"
        :type="row.health.type"
        effect="plain"
      >
        {{ row.health.label }}
      </el-tag>
      <el-tag
        size="small"
        :type="row.availability.type"
        effect="plain"
      >
        {{ row.availability.label }}
      </el-tag>
      <div class="runtime-status-table__details">
        <p
          v-for="detail in row.details"
          :key="detail"
        >
          {{ detail }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.runtime-status-table {
  width: 100%;
}

.runtime-status-table__head,
.runtime-status-table__row {
  display: grid;
  grid-template-columns:
    minmax(10rem, 15rem)
    minmax(7rem, 9rem)
    minmax(7rem, 9rem)
    minmax(9rem, 12rem)
    minmax(0, 1fr);
  gap: 12px 16px;
  align-items: center;
  padding: 0 24px;
}

.runtime-status-table__head {
  min-height: 38px;
  border-bottom: 1px solid #e5e6eb;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-weight: 650;
}

.runtime-status-table__row {
  min-height: 64px;
  padding-top: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid #e5e6eb;
  transition: background-color 0.12s ease;
}

.runtime-status-table__row:last-child {
  border-bottom: 0;
}

.runtime-status-table__row:hover {
  background-color: rgba(15, 23, 42, 0.04);
}

.runtime-status-table__service {
  color: var(--color-text-title, #1c1c26);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", sans-serif;
  font-size: 12px;
  font-weight: 650;
}

.runtime-status-table__details {
  min-width: 0;
  color: var(--color-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.45;
}

.runtime-status-table__details p {
  margin: 0;
}

.runtime-status-table__details p + p {
  margin-top: 3px;
}

@media (max-width: 960px) {
  .runtime-status-table__head,
  .runtime-status-table__row {
    grid-template-columns:
      minmax(9rem, 13rem)
      minmax(6rem, 8rem)
      minmax(6rem, 8rem)
      minmax(8rem, 10rem)
      minmax(0, 1fr);
    gap: 8px 10px;
    padding-left: 16px;
    padding-right: 16px;
  }
}

@media (max-width: 720px) {
  .runtime-status-table__head {
    display: none;
  }

  .runtime-status-table__row {
    grid-template-columns: 1fr auto;
    gap: 8px 12px;
    min-height: 0;
    padding-top: 12px;
    padding-bottom: 12px;
  }

  .runtime-status-table__service {
    grid-column: 1 / 2;
  }

  .runtime-status-table__details {
    grid-column: 1 / -1;
  }
}
</style>
