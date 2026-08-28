<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, ChevronRight, Lightbulb, LockKeyhole } from 'lucide-vue-next'

type FailureItem = {
  path: string
  error: string
}

type SkippedItem = FailureItem

const props = defineProps<{
  metadata?: unknown
}>()

const { t } = useI18n()

const metadataRecord = computed<Record<string, unknown>>(() => {
  if (!props.metadata || typeof props.metadata !== 'object' || Array.isArray(props.metadata)) return {}
  return props.metadata as Record<string, unknown>
})

const failureDetails = computed<Record<string, unknown>>(() => {
  const value = metadataRecord.value.failure_details
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
})

const skippedDetails = computed<Record<string, unknown>>(() => {
  const value = metadataRecord.value.skipped_details
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
})

const backupSummary = computed<Record<string, unknown>>(() => {
  const value = metadataRecord.value.backup_summary
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
})
const summarySnapshotId = computed(() => String(backupSummary.value.snapshot_id || '').trim())
const failedDirectories = computed(() => {
  const value = backupSummary.value.failed_directories
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    const path = String(record.path || '').trim()
    return path ? [{ path }] : []
  })
})

const category = computed(() => String(failureDetails.value.category || 'source_read_failed'))
const sourcePath = computed(() => String(metadataRecord.value.source_path || '').trim())
const items = computed<FailureItem[]>(() => {
  const value = failureDetails.value.items
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    const path = String(record.path || '').trim()
    const error = String(record.error || '').trim()
    return path || error ? [{ path, error }] : []
  })
})
const remediation = computed(() => {
  const value = failureDetails.value.remediation
  if (!Array.isArray(value)) return []
  return value.map(item => String(item || '').trim()).filter(Boolean)
})
const failureCount = computed(() => {
  const value = Number(failureDetails.value.count)
  return Number.isFinite(value) && value > 0 ? value : items.value.length
})
const skippedItems = computed<SkippedItem[]>(() => {
  const value = skippedDetails.value.items
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    const path = String(record.path || '').trim()
    const error = String(record.error || '').trim()
    return path || error ? [{ path, error }] : []
  })
})

function positiveCount(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : 0
}

const skippedFileCount = computed(() => (
  positiveCount(skippedDetails.value.file_count ?? metadataRecord.value.skipped_file_count)
))
const skippedDirectoryCount = computed(() => (
  positiveCount(skippedDetails.value.directory_count ?? metadataRecord.value.skipped_directory_count)
))
const skippedCount = computed(() => (
  positiveCount(skippedDetails.value.count ?? metadataRecord.value.skipped_item_count)
  || skippedFileCount.value + skippedDirectoryCount.value
  || skippedItems.value.length
))
const skippedReportedCount = computed(() => (
  positiveCount(skippedDetails.value.reported_count) || skippedItems.value.length
))
const skippedTruncated = computed(() => Boolean(skippedDetails.value.truncated))
const hasSkippedDetails = computed(() => skippedCount.value > 0)
const hasDetails = computed(() => (
  items.value.length > 0
  || hasSkippedDetails.value
  || Boolean(summarySnapshotId.value && failedDirectories.value.length)
))

function fullPath(path: string) {
  if (!path || !sourcePath.value) return path || sourcePath.value
  if (/^[a-z]:[\\/]/i.test(path) || path.startsWith('/') || path.startsWith('\\\\')) return path
  const separator = sourcePath.value.includes('\\') ? '\\' : '/'
  const normalizedPath = path.replace(/[\\/]+/g, separator).replace(/^[\\/]+/, '')
  return `${sourcePath.value.replace(/[\\/]+$/, '')}${separator}${normalizedPath}`
}

function failureReason(item: FailureItem) {
  if (category.value === 'source_file_locked') return t('ops.task.failureDetails.fileLockedReason')
  return item.error || t('ops.task.failureDetails.readFailedReason')
}

function remediationText(code: string) {
  const key = `ops.task.failureDetails.remediation.${code}`
  return t(key)
}
</script>

<template>
  <section
    v-if="hasDetails"
    class="task-event-failure"
    :class="{ 'task-event-failure--warning': hasSkippedDetails && !items.length }"
  >
    <template v-if="summarySnapshotId && failedDirectories.length">
      <div class="task-event-failure__summary task-event-failure__summary--neutral">
        <span>{{ t('ops.task.failureDetails.snapshotId') }}:</span>
        <code>{{ summarySnapshotId }}</code>
      </div>
      <div class="task-event-failure__label">
        {{ t('ops.task.failureDetails.failedDirectories') }}
      </div>
      <ul class="task-event-failure__directory-list">
        <li
          v-for="directory in failedDirectories"
          :key="directory.path"
        >
          <code>{{ directory.path }}</code>
        </li>
      </ul>
    </template>
    <template v-if="hasSkippedDetails">
      <div class="task-event-failure__summary">
        <AlertTriangle :size="15" />
        <span>{{ t('ops.task.failureDetails.summary.source_items_skipped', {
          count: skippedCount,
          fileCount: skippedFileCount,
          directoryCount: skippedDirectoryCount,
        }) }}</span>
      </div>

      <details
        v-if="skippedItems.length"
        class="task-event-failure__files"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.viewSkippedItems', { count: skippedReportedCount }) }}
        </summary>
        <p
          v-if="skippedTruncated"
          class="task-event-failure__truncated"
        >
          {{ t('ops.task.failureDetails.skippedItemsTruncated', {
            reportedCount: skippedReportedCount,
            count: skippedCount,
          }) }}
        </p>
        <ul>
          <li
            v-for="(item, index) in skippedItems"
            :key="`${item.path}:${index}`"
          >
            <code>{{ fullPath(item.path) }}</code>
            <span>{{ item.error || t('ops.task.failureDetails.readFailedReason') }}</span>
          </li>
        </ul>
      </details>
    </template>
    <template v-if="items.length">
      <div class="task-event-failure__summary">
        <LockKeyhole
          v-if="category === 'source_file_locked'"
          :size="15"
        />
        <AlertTriangle
          v-else
          :size="15"
        />
        <span>{{ t(`ops.task.failureDetails.summary.${category}`, { count: failureCount }) }}</span>
      </div>

      <div
        v-if="remediation.length"
        class="task-event-failure__remediation"
      >
        <div class="task-event-failure__label">
          <Lightbulb :size="14" />
          {{ t('ops.task.failureDetails.howToResolve') }}
        </div>
        <ol>
          <li
            v-for="code in remediation"
            :key="code"
          >
            {{ remediationText(code) }}
          </li>
        </ol>
      </div>

      <details class="task-event-failure__files">
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.viewAffectedFiles', { count: failureCount }) }}
        </summary>
        <ul>
          <li
            v-for="(item, index) in items"
            :key="`${item.path}:${index}`"
          >
            <code>{{ fullPath(item.path) }}</code>
            <span>{{ failureReason(item) }}</span>
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>

<style scoped>
.task-event-failure {
  display: grid;
  gap: 9px;
  max-width: 100%;
  margin-top: 4px;
  border: 1px solid rgb(254 202 202);
  border-radius: 7px;
  background: rgb(254 242 242);
  padding: 10px 12px;
  color: rgb(127 29 29);
}

.task-event-failure__summary,
.task-event-failure__label,
.task-event-failure__files summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
}

.task-event-failure__summary--neutral {
  color: rgb(51 65 85);
}

.task-event-failure--warning {
  border-color: rgb(253 230 138);
  background: rgb(255 251 235);
  color: rgb(120 53 15);
}

.task-event-failure--warning .task-event-failure__files summary,
.task-event-failure--warning .task-event-failure__files code,
.task-event-failure--warning .task-event-failure__files li span {
  color: rgb(120 53 15);
}

.task-event-failure--warning .task-event-failure__files li {
  border-top-color: rgb(253 230 138);
}

.task-event-failure__truncated {
  margin: 8px 0 0;
  font-size: 12px;
}

.task-event-failure__summary--neutral code,
.task-event-failure__directory-list code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  overflow-wrap: anywhere;
}

.task-event-failure__directory-list {
  display: grid;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.task-event-failure__directory-list li {
  display: grid;
  gap: 2px;
  border-top: 1px solid rgb(254 202 202);
  padding-top: 7px;
}

.task-event-failure__remediation {
  border-left: 3px solid rgb(251 191 36);
  background: rgb(255 251 235);
  padding: 7px 9px;
  color: rgb(120 53 15);
}

.task-event-failure__remediation ol {
  margin: 5px 0 0 18px;
  padding: 0;
}

.task-event-failure__remediation li + li {
  margin-top: 3px;
}

.task-event-failure__files summary {
  cursor: pointer;
  list-style: none;
  color: rgb(185 28 28);
}

.task-event-failure__files summary::-webkit-details-marker {
  display: none;
}

.task-event-failure__files[open] summary svg {
  transform: rotate(90deg);
}

.task-event-failure__files ul {
  display: grid;
  gap: 7px;
  margin: 9px 0 0;
  padding: 0;
  list-style: none;
}

.task-event-failure__files li {
  display: grid;
  gap: 2px;
  border-top: 1px solid rgb(254 202 202);
  padding-top: 7px;
}

.task-event-failure__files code {
  color: rgb(127 29 29);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.task-event-failure__files li span {
  color: rgb(153 27 27);
}
</style>
