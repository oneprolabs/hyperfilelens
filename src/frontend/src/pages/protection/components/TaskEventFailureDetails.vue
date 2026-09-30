<script setup lang="ts">
import { backupFailureCategory, backupFailureMetadata, backupFailurePresentation } from '../../../lib/backupFailureDisplay'
import {
  extractFailureDetails,
  extractSkippedDetails,
  extractBackupSummary,
  type FailureItem,
  type SkippedItem,
} from '../../../lib/backupTaskFailureLogic'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { AlertTriangle, ChevronRight, Copy, Lightbulb, LockKeyhole } from 'lucide-vue-next'
import { copyTextToClipboard } from '../../../lib/clipboard'

const props = withDefaults(defineProps<{
  metadata?: unknown
  showTerminalFailure?: boolean
  showSkippedDetails?: boolean
  technicalDetail?: string
  terminalResolutions?: string[]
}>(), {
  showTerminalFailure: true,
  showSkippedDetails: true,
  technicalDetail: '',
  terminalResolutions: () => [],
})

const MAX_SKIPPED_ITEMS = 10
const skippedDetailsOpen = ref(false)
const failureDetailsOpen = ref(false)

const { t } = useI18n()

const metadataRecord = computed(() => backupFailureMetadata(props.metadata))
const backupFailure = computed(() => backupFailurePresentation(metadataRecord.value))
const backupFailureCategoryValue = computed(() => backupFailureCategory(metadataRecord.value))
const backupFailureReason = computed(() => backupFailureCategoryValue.value === 'backup_communication_timeout'
  ? t('ops.task.failureDetails.communicationTimeoutReason')
  : backupFailure.value?.reason)
const backupFailureResolutions = computed(() => backupFailureCategoryValue.value === 'backup_communication_timeout'
  ? [t('ops.task.failureDetails.communicationTimeoutResolution')]
  : backupFailure.value?.resolutions || [])

// Use shared extraction functions (pure data, no Vue reactivity in them)
const structuredFailure = computed(() => extractFailureDetails(metadataRecord.value))
const structuredSkipped = computed(() => extractSkippedDetails(metadataRecord.value))
const structuredSummary = computed(() => extractBackupSummary(metadataRecord.value))

const category = computed(() => structuredFailure.value?.category || 'source_read_failed')
const backupSourceOffline = computed(() => ['backup_communication_timeout', 'backup_source_offline', 'backup_source_busy', 'backup_precheck_failed'].includes(category.value))
const sourcePath = computed(() => String(metadataRecord.value.source_path || '').trim())
const errorCode = computed(() => String(metadataRecord.value.error_code || '').trim())
const restorePermissionDenied = computed(() => errorCode.value === 'RESTORE_TARGET_PERMISSION_DENIED')
const restorePermissionMessage = computed(() => t('ops.task.failureDetails.restorePermissionDenied'))
const restorePermissionRemediation = computed(() => t('ops.task.failureDetails.restorePermissionRemediation'))
const restorePermissionRemediationItems = computed(() => restorePermissionRemediation.value.split('\n').map(item => item.replace(/^\d+\.\s*/, '').trim()).filter(Boolean))
const restoreTargetPath = computed(() => String(metadataRecord.value.target_path || '').trim())
const errorDiagnostic = computed(() => String(metadataRecord.value.error_diagnostic || '').trim())
const originalError = computed(() => String(metadataRecord.value.error_message || '').trim())
const terminalContext = computed(() => {
  const value = metadataRecord.value.terminal_failure
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {} as Record<string, unknown>
})
const exactSourceBusy = computed(() =>
  props.showTerminalFailure
  && terminalContext.value.side === 'source'
  && terminalContext.value.confidence === 'exact'
  && Boolean(terminalContext.value.path)
  && /device or resource busy/i.test(String(terminalContext.value.message || originalError.value)),
)
const terminalFailure = computed(() => {
  if (!props.showTerminalFailure) return ''
  if (exactSourceBusy.value) return t('ops.task.failureDetails.sourceBusyTitle')
  const terminal = metadataRecord.value.terminal_failure
  if (terminal && typeof terminal === 'object' && !Array.isArray(terminal)) {
    const message = String((terminal as Record<string, unknown>).message || '').trim()
    if (message) return message
  }
  return errorCode.value ? originalError.value : ''
})
const showTerminalBox = computed(() =>
  Boolean(terminalFailure.value) && !failureCount.value && !backupSourceOffline.value && !restorePermissionDenied.value,
)
const terminalFailurePath = computed(() => {
  const terminal = metadataRecord.value.terminal_failure
  if (!terminal || typeof terminal !== 'object' || Array.isArray(terminal)) return ''
  return String((terminal as Record<string, unknown>).path || '').trim()
})
const terminalFilterRule = computed(() =>
  exactSourceBusy.value ? String(terminalContext.value.filter_rule || '') : '',
)
const effectiveTerminalResolutions = computed(() =>
  exactSourceBusy.value
    ? [
        t('ops.task.failureDetails.sourceBusyCheck'),
        t('ops.task.failureDetails.sourceBusyRetry'),
      ]
    : props.terminalResolutions,
)
async function copyFilterRule() {
  try {
    await copyTextToClipboard(terminalFilterRule.value)
    ElMessage.success(t('feedback.toast.copied'))
  } catch {
    ElMessage.error(t('ops.task.msgCopyFailed'))
  }
}

const items = computed<FailureItem[]>(() => structuredFailure.value?.items || [])
const causes = computed(() => structuredFailure.value?.causes || [])
const remediation = computed(() => structuredFailure.value?.remediation || [])
const failureCount = computed(() => structuredFailure.value?.total_count || 0)
const reportedFailureCount = computed(() => structuredFailure.value?.reported_count || 0)
const failureTruncated = computed(() => Boolean(structuredFailure.value?.truncated) || (structuredFailure.value?.total_count ?? 0) > (structuredFailure.value?.reported_count ?? 0))

const skippedItems = computed<SkippedItem[]>(() => structuredSkipped.value?.items || [])
const skippedCount = computed(() => structuredSkipped.value?.count || 0)
const skippedFileCount = computed(() => structuredSkipped.value?.file_count || 0)
const skippedDirectoryCount = computed(() => structuredSkipped.value?.directory_count || 0)
const skippedSpecialCount = computed(() => structuredSkipped.value?.special_count || 0)
const skippedReportedCount = computed(() => Math.min(MAX_SKIPPED_ITEMS, structuredSkipped.value?.reported_count || 0))
const hasSkippedDetails = computed(() => props.showSkippedDetails && skippedCount.value > 0)
const technicalDetail = computed(() => String(props.technicalDetail || '').trim())

const summarySnapshotId = computed(() => structuredSummary.value?.snapshot_id || '')
const summaryRestoreRecordId = computed(() => structuredSummary.value?.restore_record_id || '')
const failedDirectories = computed(() => structuredSummary.value?.failed_directories || [])

const hasDetails = computed(() => (
  items.value.length > 0
  || causes.value.length > 0
  || failureCount.value > 0
  || hasSkippedDetails.value
  || restorePermissionDenied.value
  || Boolean(terminalFailure.value)
  || Boolean(summarySnapshotId.value && failedDirectories.value.length)
  || Boolean(summaryRestoreRecordId.value && failedDirectories.value.length)
  || backupSourceOffline.value
  || category.value === 'BACKUP_TARGET_STORAGE_FULL'
))

function fullPath(path: string) {
  if (!path || !sourcePath.value) return path || sourcePath.value
  if (/^[a-z]:[\\/]/i.test(path) || path.startsWith('/') || path.startsWith('\\\\')) return path
  const separator = sourcePath.value.includes('\\') ? '\\' : '/'
  const normalizedPath = path.replace(/[\\/]+/g, separator).replace(/^[\\/]+/, '')
  return `${sourcePath.value.replace(/[\\/]+$/, '')}${separator}${normalizedPath}`
}

function failureReason(item: FailureItem) {
  if (item.cause === 'source_resource_busy') return t('ops.task.failureDetails.sourceResourceBusyReason')
  if (category.value === 'source_file_locked') return t('ops.task.failureDetails.fileLockedReason')
  if (item.cause === 'unsupported_entry_type') return t('ops.task.failureDetails.unsupportedEntryReason')
  if (item.cause === 'macos_privacy_denied') return t('ops.task.failureDetails.macosPrivacyReason')
  if (item.cause === 'permission_denied') return t('ops.task.failureDetails.permissionDeniedReason')
  if (item.cause === 'unreadable_directory') return t('ops.task.failureDetails.unreadableDirectoryReason')
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
    :class="{
      'task-event-failure--warning': hasSkippedDetails && !items.length && !failureCount && !showTerminalBox,
      'task-event-failure--mixed': showTerminalBox && hasSkippedDetails,
    }"
  >
    <template v-if="backupSourceOffline">
      <div class="task-event-failure__summary">
        <AlertTriangle :size="15" />
        <span>{{ backupFailureReason }}</span>
      </div>
      <div class="task-event-failure__remediation">
        <div class="task-event-failure__label">
          <Lightbulb :size="14" />
          {{ t('ops.task.failureDetails.howToResolve') }}
        </div>
        <ol class="task-event-failure__remediation-list">
          <li
            v-for="resolution in backupFailureResolutions"
            :key="resolution"
          >
            {{ resolution }}
          </li>
        </ol>
      </div>
      <details
        v-if="originalError"
        class="task-event-failure__files"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.technicalDetails') }}
        </summary>
        <code>{{ originalError }}</code>
      </details>
    </template>
    <template v-if="(summarySnapshotId || summaryRestoreRecordId) && failedDirectories.length">
      <div class="task-event-failure__summary task-event-failure__summary--neutral">
        <span>{{ t(summaryRestoreRecordId ? 'ops.task.failureDetails.restoreRecordId' : 'ops.task.failureDetails.snapshotId') }}:</span>
        <code>{{ summaryRestoreRecordId || summarySnapshotId }}</code>
      </div>
      <div class="task-event-failure__label task-event-failure__label--directory">
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
    <div
      v-if="showTerminalBox"
      class="task-event-failure__terminal-box"
    >
      <div class="task-event-failure__summary task-event-failure__summary--terminal">
        <AlertTriangle :size="15" />
        <div class="task-event-failure__terminal-copy">
          <span>{{ terminalFailure }}</span>
        </div>
      </div>
      <div
        v-if="terminalFailurePath"
        class="task-event-failure__terminal-path"
      >
        {{ t('ops.task.failureDetails.affectedPath') }}: <code>{{ terminalFailurePath }}</code>
      </div>
      <div
        v-if="effectiveTerminalResolutions.length || terminalFilterRule"
        class="task-event-failure__remediation"
      >
        <div class="task-event-failure__label">
          <Lightbulb :size="14" />
          {{ t('ops.task.failureDetails.howToResolve') }}
        </div>
        <ol class="task-event-failure__remediation-list">
          <li
            v-for="resolution in effectiveTerminalResolutions"
            :key="resolution"
          >
            {{ resolution }}
          </li>
          <li v-if="terminalFilterRule">
            {{ t('ops.task.failureDetails.sourceBusyExclude') }}
            <code class="task-event-failure__filter-rule">{{ terminalFilterRule }}</code>
            <button
              type="button"
              class="task-event-failure__copy-rule"
              :aria-label="t('ops.task.failureDetails.copyFilterRule')"
              @click="copyFilterRule"
            >
              <Copy :size="13" />
              {{ t('feedback.toast.copy') }}
            </button>
          </li>
        </ol>
      </div>
      <details
        v-if="technicalDetail"
        class="task-event-failure__technical"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.technicalDetails') }}
        </summary>
        <pre>{{ technicalDetail }}</pre>
      </details>
    </div>
    <div
      v-if="hasSkippedDetails"
      :class="{ 'task-event-failure__skipped-box': Boolean(terminalFailure) }"
    >
      <div class="task-event-failure__summary task-event-failure__summary--warning">
        <AlertTriangle :size="15" />
        <span>{{ t('ops.task.failureDetails.summary.source_items_skipped', {
          count: skippedCount,
          fileCount: skippedFileCount,
          directoryCount: skippedDirectoryCount,
          specialCount: skippedSpecialCount,
        }) }}</span>
      </div>

      <details
        v-if="skippedItems.length"
        class="task-event-failure__files task-event-failure__files--warning"
        @toggle="skippedDetailsOpen = ($event.currentTarget as HTMLDetailsElement).open"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t(skippedDetailsOpen ? 'ops.task.failureDetails.collapseSkippedItems' : 'ops.task.failureDetails.viewSkippedItems', { reportedCount: skippedReportedCount }) }}
        </summary>
        <p class="task-event-failure__coverage">
          {{ t('ops.task.failureDetails.skippedItemsTruncated', { reportedCount: skippedReportedCount, count: skippedCount, omittedCount: Math.max(0, skippedCount - skippedReportedCount) }) }}
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
    </div>
    <template v-if="restorePermissionDenied">
      <div class="task-event-failure__summary">
        <LockKeyhole :size="15" />
        <span>{{ restorePermissionMessage }}</span>
      </div>
      <div
        v-if="restoreTargetPath"
        class="task-event-failure__summary task-event-failure__summary--neutral"
      >
        <span>{{ t('ops.task.failureDetails.restoreTarget') }}:</span>
        <code>{{ restoreTargetPath }}</code>
      </div>
      <div class="task-event-failure__remediation">
        <div class="task-event-failure__label">
          <Lightbulb :size="14" />
          {{ t('ops.task.failureDetails.howToResolve') }}
        </div>
        <ol class="task-event-failure__remediation-list">
          <li
            v-for="item in restorePermissionRemediationItems"
            :key="item"
          >
            {{ item }}
          </li>
        </ol>
      </div>
      <details
        v-if="errorDiagnostic"
        class="task-event-failure__files"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.technicalDetails') }}
        </summary>
        <code>{{ errorDiagnostic }}</code>
      </details>
    </template>
    <template v-if="!backupSourceOffline && (failureCount > 0 || causes.length || category === 'BACKUP_TARGET_STORAGE_FULL')">
      <div class="task-event-failure__summary task-event-failure__summary--structured">
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
        <ol class="task-event-failure__remediation-list">
          <li
            v-for="code in remediation"
            :key="code"
          >
            {{ remediationText(code) }}
          </li>
        </ol>
      </div>

      <p
        v-if="failureTruncated && !items.length"
        class="task-event-failure__truncated"
      >
        {{ t('ops.task.failureDetails.failureItemsTruncated', {
          reportedCount: reportedFailureCount,
          count: failureCount,
          omittedCount: Math.max(0, failureCount - reportedFailureCount),
        }) }}
      </p>

      <details
        v-if="category === 'BACKUP_TARGET_STORAGE_FULL' && (errorDiagnostic || originalError || items.length)"
        class="task-event-failure__files"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.viewOriginalError') }}
        </summary>
        <code v-if="errorDiagnostic || originalError">{{ errorDiagnostic || originalError }}</code>
        <ul v-if="items.length">
          <li
            v-for="(item, index) in items"
            :key="`${item.path}:${index}`"
          >
            <code>{{ item.path }}</code>
            <span>{{ item.error }}</span>
          </li>
        </ul>
      </details>
      <details
        v-else-if="items.length"
        class="task-event-failure__files"
        @toggle="failureDetailsOpen = ($event.currentTarget as HTMLDetailsElement).open"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t(failureDetailsOpen ? 'ops.task.failureDetails.collapseAffectedItems' : 'ops.task.failureDetails.viewAffectedItems', { reportedCount: reportedFailureCount }) }}
        </summary>
        <p class="task-event-failure__coverage">
          {{ t('ops.task.failureDetails.failureItemsTruncated', { reportedCount: reportedFailureCount, count: failureCount, omittedCount: Math.max(0, failureCount - reportedFailureCount) }) }}
        </p>
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
      <details
        v-if="!items.length && category !== 'BACKUP_TARGET_STORAGE_FULL' && (technicalDetail || errorDiagnostic)"
        class="task-event-failure__technical"
      >
        <summary>
          <ChevronRight :size="14" />
          {{ t('ops.task.failureDetails.technicalDetails') }}
        </summary>
        <pre>{{ technicalDetail || errorDiagnostic }}</pre>
      </details>
    </template>
  </section>
</template>

<style scoped>
.task-event-failure {
  display: grid;
  align-self: stretch;
  gap: 9px;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
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
  font-size: 12px;
}

.task-event-failure__summary--warning {
  color: rgb(120 53 15);
  font-size: 12px;
}

.task-event-failure__label--directory {
  font-size: 12px;
}

.task-event-failure__summary--terminal {
  padding: 0;
  color: rgb(127 29 29);
}

.task-event-failure__summary--structured {
  color: rgb(127 29 29);
  font-size: 12px;
  line-height: 1.5;
}

.task-event-failure__terminal-box {
  display: grid;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid rgb(254 202 202);
  border-radius: 7px;
  background: rgb(254 242 242);
}

.task-event-failure__terminal-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.task-event-failure__terminal-copy strong {
  color: rgb(153 27 27);
  font-size: 12px;
  font-weight: 800;
}

.task-event-failure__terminal-copy span {
  color: rgb(127 29 29);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.task-event-failure__terminal-path {
  padding: 0 0 1px;
  color: rgb(127 29 29);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.task-event-failure__summary--terminal + .task-event-failure__terminal-path {
  margin-top: -7px;
}

.task-event-failure__terminal-path code,
.task-event-failure__filter-rule {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.task-event-failure__filter-rule {
  display: inline-block;
  margin-left: 4px;
  padding: 2px 5px;
  border-radius: 4px;
  background: rgb(255 255 255 / 76%);
  user-select: all;
}

.task-event-failure__copy-rule {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 6px;
  padding: 2px 4px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.task-event-failure--mixed {
  border: 0;
  background: transparent;
  padding: 0;
  color: inherit;
}

.task-event-failure__skipped-box {
  display: grid;
  gap: 0;
  margin-top: 8px;
  border: 1px solid rgb(253 230 138);
  border-radius: 7px;
  background: rgb(255 251 235);
  color: rgb(120 53 15);
  overflow: hidden;
}

.task-event-failure__skipped-box .task-event-failure__summary--warning {
  padding: 10px 12px;
}

.task-event-failure__skipped-box .task-event-failure__files--warning {
  margin: 0 10px 10px;
  border: 1px solid rgb(253 230 138);
  background: rgb(255 255 255 / 60%);
}

.task-event-failure__technical {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid rgb(252 165 165 / 72%);
  border-radius: 7px;
  background: rgb(255 255 255 / 82%);
}

.task-event-failure__technical summary {
  display: flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  color: rgb(153 27 27);
  font-size: 12px;
  font-weight: 700;
  list-style: none;
}

.task-event-failure__technical summary::-webkit-details-marker {
  display: none;
}

.task-event-failure__technical[open] summary svg {
  transform: rotate(90deg);
}

.task-event-failure__technical pre {
  max-height: 220px;
  margin: 8px 0 0;
  overflow: auto;
  color: rgb(127 29 29);
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.task-event-failure--warning {
  gap: 0;
  padding: 0;
  border-color: rgb(253 230 138);
  background: rgb(255 251 235);
  color: rgb(120 53 15);
}

.task-event-failure--warning .task-event-failure__summary--warning {
  padding: 10px 12px;
}

.task-event-failure--warning .task-event-failure__files summary,
.task-event-failure--warning .task-event-failure__files code,
.task-event-failure--warning .task-event-failure__files li span {
  color: rgb(120 53 15);
}

.task-event-failure--warning .task-event-failure__files li {
  border-top-color: rgb(253 230 138);
}

.task-event-failure__files {
  margin-top: 2px;
  padding: 9px 10px;
  border: 1px solid rgb(254 202 202);
  border-radius: 7px;
  background: rgb(255 255 255 / 72%);
}

.task-event-failure--warning .task-event-failure__files {
  margin: 0 10px 10px;
}

.task-event-failure__files--warning {
  margin: 0 10px 10px;
  border-color: rgb(253 230 138);
  background: rgb(255 255 255 / 60%);
}

.task-event-failure__files--warning summary,
.task-event-failure__files--warning code,
.task-event-failure__files--warning li span {
  color: rgb(120 53 15);
}

.task-event-failure__files--warning li {
  border-top-color: rgb(253 230 138);
}

.task-event-failure__truncated {
  margin: 8px 0 0;
  font-size: 12px;
}

.task-event-failure__coverage {
  margin: 8px 0 0;
  color: rgb(120 53 15);
  font-size: 12px;
  line-height: 1.45;
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
  font-size: 12px;
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

.task-event-failure__remediation p {
  margin: 5px 0 0;
}

.task-event-failure__numbered-remediation {
  white-space: pre-line;
}

.task-event-failure__remediation ol {
  margin: 5px 0 0 18px;
  padding: 0;
  list-style-type: decimal !important;
  list-style-position: outside;
}

.task-event-failure__remediation-list li::marker {
  color: rgb(146 64 14);
  font-weight: 700;
}

.task-event-failure__remediation li + li {
  margin-top: 3px;
}

.task-event-failure__files summary {
  cursor: pointer;
  list-style: none;
  color: rgb(185 28 28);
  font-size: 12px;
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

.task-event-failure__files > code {
  display: block;
  margin-top: 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.task-event-failure__files li span {
  color: rgb(153 27 27);
}

/* Keep skipped-item details aligned with the warning title, after the
 * generic failure-detail rules above so the warning color cannot be reset. */
.task-event-failure__files--warning summary,
.task-event-failure__files--warning code,
.task-event-failure__files--warning li span {
  color: rgb(120 53 15);
  font-size: 12px;
}

.task-event-failure__files--warning summary {
  font-weight: 700;
}
</style>
