<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { RuntimeStatusNoticeLevel, RuntimeStatusRow } from './runtimeStatus'

defineProps<{
  rows: RuntimeStatusRow[]
  hideHeader?: boolean
}>()

const { t } = useI18n()

const noticeLabelKeys: Record<RuntimeStatusNoticeLevel, string> = {
  info: 'platformOps.settings.environment.noticeInfo',
  warning: 'platformOps.settings.environment.noticeWarning',
  error: 'platformOps.settings.environment.noticeError',
}

function detailParts(detail: string): Array<{ text: string; href?: string }> {
  const parts: Array<{ text: string; href?: string }> = []
  const urlPattern = /https?:\/\/[^\s]+/g
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = urlPattern.exec(detail))) {
    if (match.index > cursor) parts.push({ text: detail.slice(cursor, match.index) })
    parts.push({ text: match[0], href: match[0] })
    cursor = match.index + match[0].length
  }
  if (cursor < detail.length) parts.push({ text: detail.slice(cursor) })
  return parts.length ? parts : [{ text: detail }]
}
</script>

<template>
  <div class="runtime-status-table">
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
          <template
            v-for="(part, index) in detailParts(detail)"
            :key="`${detail}-${index}`"
          >
            <a
              v-if="part.href"
              :href="part.href"
              target="_blank"
              rel="noopener noreferrer"
            >{{ part.text }}</a>
            <template v-else>
              {{ part.text }}
            </template>
          </template>
        </p>
        <p
          v-for="notice in row.notices || []"
          :key="`${notice.level}-${notice.message}`"
          class="runtime-status-table__notice"
          :class="`runtime-status-table__notice--${notice.level}`"
        >
          <strong>{{ t(noticeLabelKeys[notice.level]) }}:</strong>
          {{ notice.message }}
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
    minmax(13rem, 1.2fr)
    minmax(9rem, 0.9fr)
    minmax(9rem, 0.9fr)
    minmax(11rem, 1.1fr)
    minmax(20rem, 2.4fr);
  gap: 12px 16px;
  align-items: center;
  padding: 0 24px;
}

.runtime-status-table__head {
  min-height: 38px;
  border-bottom: 1px solid #e5e6eb;
  color: var(--color-text-secondary, #70707e);
  font-size: 12px;
  font-weight: 600;
}

.runtime-status-table__row {
  min-height: 64px;
  padding-top: 14px;
  padding-bottom: 14px;
  border-bottom: 1px solid #e5e6eb;
  transition: background-color 0.12s ease;
}

.runtime-status-table__row:last-child {
  border-bottom: 0;
}

.runtime-status-table__row:hover {
  background-color: rgba(15, 23, 42, 0.08);
}

.runtime-status-table__service {
  color: var(--color-text-title, #1c1c26);
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.35;
}

.runtime-status-table__details {
  min-width: 0;
  color: var(--color-text-secondary, #70707e);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.45;
}

.runtime-status-table__details p {
  margin: 0;
}

.runtime-status-table__details p + p {
  margin-top: 3px;
}

.runtime-status-table__notice {
  margin: 6px 0 0;
  padding: 4px 8px;
  border-radius: 4px;
}

.runtime-status-table__notice--info {
  border-left: 2px solid #60a5fa;
  background: #eff6ff;
  color: #1d4ed8;
}

.runtime-status-table__notice--warning {
  border-left: 2px solid #f59e0b;
  background: #fffbeb;
  color: #b45309;
}

.runtime-status-table__notice--error {
  border-left: 2px solid #ef4444;
  background: #fef2f2;
  color: #b42318;
}

.runtime-status-table__notice strong {
  font-weight: 600;
}

.runtime-status-table__details a {
  color: var(--color-primary);
  text-decoration: none;
}

.runtime-status-table__details a:hover {
  text-decoration: underline;
}

.runtime-status-table .el-tag {
  justify-self: start;
  width: auto;
  min-width: 0;
}

.runtime-status-table .el-tag {
  background: transparent !important;
}

.runtime-status-table .el-tag--info {
  border-color: #bfdbfe;
  color: #2563eb;
}

.runtime-status-table .el-tag--success {
  border-color: #86efac;
  color: #15803d;
}

.runtime-status-table .el-tag--warning {
  border-color: #fcd34d;
  color: #b45309;
}

.runtime-status-table .el-tag--danger {
  border-color: #fca5a5;
  color: #b91c1c;
}

@media (max-width: 960px) {
  .runtime-status-table__head,
  .runtime-status-table__row {
    grid-template-columns:
      minmax(11rem, 1.1fr)
      minmax(8rem, 0.9fr)
      minmax(8rem, 0.9fr)
      minmax(10rem, 1.1fr)
      minmax(16rem, 1.8fr);
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
