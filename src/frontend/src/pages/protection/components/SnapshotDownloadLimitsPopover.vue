<script setup lang="ts">
import { Info, RotateCcw } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import HflPopover from '../../../components/HflPopover.vue'

defineProps<{
  maxItems: number
  maxSizeBytes: number
}>()

const { t } = useI18n()

function formatLimitBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let normalized = value
  let unitIndex = 0
  while (normalized >= 1024 && unitIndex < units.length - 1) {
    normalized /= 1024
    unitIndex += 1
  }
  const decimals = Number.isInteger(normalized) ? 0 : 1
  return `${normalized.toFixed(decimals)} ${units[unitIndex]}`
}
</script>

<template>
  <HflPopover
    placement="bottom-end"
    :width="312"
    popper-class="snapshot-download-limits-popper"
  >
    <template #reference>
      <button
        type="button"
        class="snapshot-download-limits__trigger"
        :aria-label="t('protection.backupsPage.snapshotBrowserDownloadLimitsTitle')"
      >
        <Info
          :size="15"
          :stroke-width="2.2"
          aria-hidden="true"
        />
      </button>
    </template>

    <div class="snapshot-download-limits">
      <div class="snapshot-download-limits__title">
        {{ t('protection.backupsPage.snapshotBrowserDownloadLimitsTitle') }}
      </div>
      <dl class="snapshot-download-limits__rules">
        <div class="snapshot-download-limits__rule">
          <dt>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsItems') }}</dt>
          <dd>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsItemsValue', { n: maxItems }) }}</dd>
        </div>
        <div class="snapshot-download-limits__rule">
          <dt>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsSize') }}</dt>
          <dd>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsSizeValue', { size: formatLimitBytes(maxSizeBytes) }) }}</dd>
        </div>
        <div class="snapshot-download-limits__rule">
          <dt>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsFolders') }}</dt>
          <dd>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsFoldersValue') }}</dd>
        </div>
      </dl>
      <div class="snapshot-download-limits__restore-hint">
        <RotateCcw
          :size="15"
          aria-hidden="true"
        />
        <span>{{ t('protection.backupsPage.snapshotBrowserDownloadLimitsRestoreHint') }}</span>
      </div>
    </div>
  </HflPopover>
</template>

<style scoped>
.snapshot-download-limits__trigger {
  display: inline-flex;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid rgb(203 213 225);
  border-radius: 7px;
  background: rgb(255 255 255);
  color: rgb(100 116 139);
  cursor: help;
  transition: border-color 150ms ease, background-color 150ms ease, color 150ms ease;
}

.snapshot-download-limits__trigger:hover,
.snapshot-download-limits__trigger:focus-visible {
  border-color: color-mix(in srgb, var(--color-primary) 55%, rgb(203 213 225));
  background: color-mix(in srgb, var(--color-primary) 8%, white);
  color: var(--color-primary);
  outline: none;
}

.snapshot-download-limits {
  color: rgb(51 65 85);
}

.snapshot-download-limits__title {
  padding: 11px 14px 9px;
  border-bottom: 1px solid rgb(226 232 240);
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
}

.snapshot-download-limits__rules {
  margin: 0;
  padding: 5px 14px;
}

.snapshot-download-limits__rule {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  padding: 7px 0;
}

.snapshot-download-limits__rule + .snapshot-download-limits__rule {
  border-top: 1px solid rgb(241 245 249);
}

.snapshot-download-limits__rule dt,
.snapshot-download-limits__rule dd {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
}

.snapshot-download-limits__rule dt {
  color: rgb(100 116 139);
  font-weight: 600;
}

.snapshot-download-limits__rule dd {
  color: rgb(30 41 59);
}

.snapshot-download-limits__restore-hint {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 9px 14px 10px;
  border-top: 1px solid rgb(224 231 255);
  background: rgb(238 242 255 / 72%);
  color: rgb(67 56 202);
  font-size: 12px;
  line-height: 1.45;
}

.snapshot-download-limits__restore-hint svg {
  flex: 0 0 auto;
  margin-top: 1px;
}

:global(.snapshot-download-limits-popper.el-popper) {
  z-index: 3800 !important;
  max-width: min(312px, calc(100vw - 24px)) !important;
  overflow: hidden;
  padding: 0 !important;
  border-color: rgb(203 213 225) !important;
  border-radius: 10px !important;
  background: rgb(255 255 255) !important;
  box-shadow: 0 14px 34px rgb(15 23 42 / 16%) !important;
}

:global(.snapshot-download-limits-popper.el-popper .el-popper__arrow::before) {
  border-color: rgb(203 213 225) !important;
  background: rgb(255 255 255) !important;
}
</style>
