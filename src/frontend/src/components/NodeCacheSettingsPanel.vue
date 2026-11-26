<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, ChevronUp, Info, RefreshCw } from 'lucide-vue-next'
import { ElAlert, ElButton, ElIcon, ElInput, ElTooltip, useLocale } from 'element-plus'
import DangerConfirmDialog from './DangerConfirmDialog.vue'
import { apiErrorMessageI18n } from '../lib/api'
import { blockingGiBToBytes, bytesToGiBInput, cacheWarningBytes, GIB, getCacheTask, getNodeCache, localizeCacheError, nodeCacheCapability, operateNodeCache, type CacheRelocation, type CacheTask, type NodeCacheSnapshot } from '../lib/nodeCacheApi'
import type { ApiNode } from '../types/node'

const props = withDefaults(defineProps<{ node: ApiNode; active?: boolean; saving?: boolean }>(), { active: true, saving: false })
const emit = defineEmits<{ capability: [value: 'supported' | 'legacy' | 'unknown'] }>()
const { t } = useI18n()
const { t: componentText } = useLocale()
const snapshot = ref<NodeCacheSnapshot | null>(null)
const blocking = ref('10')
const policySnapshot = ref<number | null>(null)
const retryPolicy = ref(false)
const parent = ref('')
const changeDirectoryOpen = ref(false)
const error = ref('')
const errorOperation = ref('')
const busy = ref(false)
const confirming = ref(false)
const task = ref<CacheTask | null>(null)
const validation = ref<CacheTask | null>(null)
const confirmOpen = ref(false)
let controller = new AbortController()
const capability = computed(() => nodeCacheCapability(props.node))
const loaded = computed(() => snapshot.value !== null)
const supported = computed(() => snapshot.value?.supported === true)
const blockingBytes = computed(() => blockingGiBToBytes(blocking.value))
const hasPolicyChanges = computed(() => supported.value && policySnapshot.value !== null && (retryPolicy.value || blockingBytes.value !== policySnapshot.value))
const warningBytes = computed(() => {
  if (blockingBytes.value == null) return null
  // Merely viewing an existing decimal-GB policy must not change either threshold.
  if (!hasPolicyChanges.value && snapshot.value?.policy) return snapshot.value.policy.warning_bytes
  return cacheWarningBytes(blockingBytes.value)
})
const binaryWarning = computed(() => blockingBytes.value != null && warningBytes.value != null && warningBytes.value - blockingBytes.value === 10 * GIB)
const report = computed(() => snapshot.value?.report)
const online = computed(() => props.node.routable !== false && props.node.availability !== 'offline')
const pendingTask = computed(() => task.value != null && ['pending', 'running'].includes(task.value.status))
const relocation = computed(() => {
  const result = task.value?.result
  const progress = result?.last_progress as { relocation?: CacheRelocation } | undefined
  return progress?.relocation || report.value?.relocation
})
const visibleRelocation = computed(() => relocation.value?.stage === 'completed' ? null : relocation.value)
const relocationAlertType = computed(() => {
  if (visibleRelocation.value?.stage === 'switched_cleanup_pending') return 'warning'
  return visibleRelocation.value?.stage === 'failed' || visibleRelocation.value?.error ? 'error' : 'info'
})
const relocationHelp = computed(() => {
  if (visibleRelocation.value?.stage === 'switched_cleanup_pending') return t('nodeCache.cleanupPendingHelp')
  if (visibleRelocation.value?.stage === 'failed') return t('nodeCache.relocationFailedHelp')
  return t(visibleRelocation.value?.error ? 'nodeCache.operationFailed' : 'nodeCache.relocationProgressHelp')
})
const panelError = computed(() => {
  const currentRelocation = visibleRelocation.value
  const isDirectoryOperation = ['relocate', 'retry_cleanup', 'cache.relocate', 'cache.relocation.retry'].includes(errorOperation.value)
  const isDirectoryTask = isDirectoryOperation && task.value && ['cache.relocate', 'cache.relocation.retry'].includes(task.value.kind)
  // Only deduplicate the same operation's identical inline relocation error.
  // Validation, refresh and policy failures must remain visible independently.
  if (isDirectoryTask && currentRelocation?.task_id === task.value?.id && currentRelocation?.error === error.value) return ''
  return error.value
})
const isTargetValidation = computed(() => ['validate_destination', 'cache.relocation.validate'].includes(errorOperation.value))
const directoryError = computed(() => changeDirectoryOpen.value && isTargetValidation.value ? panelError.value : '')
const generalError = computed(() => directoryError.value ? '' : panelError.value)
function clearDirectoryError() {
  if (isTargetValidation.value) { error.value = ''; errorOperation.value = '' }
}
const cachePolicyError = computed(() => {
  if (!hasPolicyChanges.value) return ''
  if (blockingBytes.value == null) return t('nodeCache.invalidBlocking')
  if (snapshot.value?.policy_unit_supported === false) return t('nodeCache.gibUpgradeRequired')
  if (!online.value) return t('nodeCache.offline')
  return ''
})
// Waiting is an operation state, not a field validation error.
const cacheOperationPending = computed(() => busy.value || pendingTask.value)
const directoryOperationPending = computed(() => cacheOperationPending.value
  && ['relocate', 'retry_cleanup', 'cache.relocate', 'cache.relocation.retry'].includes(errorOperation.value))
const thresholdEditingBlocked = computed(() => props.saving
  || (cacheOperationPending.value && !['refresh', 'cache.status'].includes(errorOperation.value))
  || snapshot.value?.policy_unit_supported === false)
const maxBlockingBytes = Number.MAX_SAFE_INTEGER - 10 * GIB
const decreaseBlocked = computed(() => thresholdEditingBlocked.value || blockingBytes.value == null || blockingBytes.value <= GIB)
const increaseBlocked = computed(() => thresholdEditingBlocked.value || (blockingBytes.value != null && blockingBytes.value > maxBlockingBytes - GIB))
function stepBlocking(direction: 1 | -1) {
  if (direction === 1 ? increaseBlocked.value : decreaseBlocked.value) return
  // Preserve exact byte-valued legacy policies rather than rounding their GiB
  // representation through a number model. Each click changes exactly one GiB.
  const next = (blockingBytes.value ?? 0) + direction * GIB
  blocking.value = bytesToGiBInput(next)
}
const policyWaitNotice = computed(() => {
  if (directoryOperationPending.value) return t('nodeCache.directoryOperationPending')
  if (!hasPolicyChanges.value || !cacheOperationPending.value || props.saving) return ''
  if (['refresh', 'cache.status'].includes(errorOperation.value)) return t('nodeCache.refreshingStatus')
  if (['save_policy', 'cache.policy.apply'].includes(errorOperation.value)) return ''
  return t('nodeCache.operationInProgress')
})
const checkedAt = computed(() => report.value?.space?.sampled_at)

function bytes(value: number | null | undefined): string {
  return value == null ? '—' : `${(value / GIB).toLocaleString(undefined, { maximumFractionDigits: 3 })} GiB`
}
function timestamp(value: string | undefined): string { return value ? new Date(value).toLocaleString() : '—' }
function cacheErrorText(message: string): string { return localizeCacheError(message, t) }
async function reload(signal: AbortSignal) {
  const result = await getNodeCache(props.node.id, signal)
  if (signal.aborted) return
  const preserveDraft = hasPolicyChanges.value
  snapshot.value = result
  emit('capability', result.supported ? 'supported' : capability.value === 'legacy' ? 'legacy' : 'unknown')
  if (!task.value && result.last_task) task.value = result.last_task
  if (result.policy) {
    policySnapshot.value = result.policy.blocking_bytes
    if (!preserveDraft) blocking.value = bytesToGiBInput(result.policy.blocking_bytes)
  }
}
function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve() }
    const timer = setTimeout(done, 1500)
    signal.addEventListener('abort', done, { once: true })
    if (signal.aborted) done()
  })
}
async function run(body: Record<string, unknown>, existing?: CacheTask): Promise<CacheTask | null> {
  if (busy.value) return null
  const signal = controller.signal
  const nodeId = props.node.id
  busy.value = true
  error.value = ''
  errorOperation.value = existing?.kind || String(body.operation || '')
  try {
    let current = existing || await operateNodeCache(nodeId, body, signal)
    if (signal.aborted) return null
    task.value = current
    const deadline = Date.now() + 120_000
    while (['pending', 'running'].includes(current.status) && Date.now() < deadline) {
      await pause(signal)
      if (signal.aborted) return null
      current = await getCacheTask(nodeId, current.id, signal)
      if (signal.aborted) return null
      task.value = current
    }
    if (!['success', 'pending', 'running'].includes(current.status)) error.value = current.error || t('nodeCache.operationFailed')
    await reload(signal)
    return signal.aborted ? null : current
  } catch (cause) {
    if (!signal.aborted) error.value = apiErrorMessageI18n(cause, t, t('nodeCache.operationFailed'))
    return null
  } finally {
    if (!signal.aborted) busy.value = false
  }
}
function cachePolicyDirty() { return hasPolicyChanges.value }
async function saveCachePolicy() {
  if (!hasPolicyChanges.value) return null
  if (cacheOperationPending.value) throw new Error(t('nodeCache.operationInProgress'))
  if (cachePolicyError.value || blockingBytes.value == null) throw new Error(cachePolicyError.value || t('nodeCache.invalidBlocking'))
  const savedBytes = blockingBytes.value
  const signal = controller.signal
  validation.value = null
  const result = await run({ operation: 'save_policy', blocking_bytes: savedBytes })
  if (signal.aborted) throw new Error(t('nodeCache.operationFailed'))
  if (!result || !['success', 'pending', 'running'].includes(result.status)) {
    retryPolicy.value = true
    throw new Error(result?.error || error.value || t('nodeCache.operationFailed'))
  }
  retryPolicy.value = false
  policySnapshot.value = savedBytes
  blocking.value = bytesToGiBInput(savedBytes)
  return result
}
function cancelCacheEdits() {
  retryPolicy.value = false
  if (policySnapshot.value !== null) blocking.value = bytesToGiBInput(policySnapshot.value)
}
function openDirectoryEditor() { changeDirectoryOpen.value = true }
function cancelDirectoryEditor() {
  clearDirectoryError()
  changeDirectoryOpen.value = false
  parent.value = ''
  validation.value = null
  confirmOpen.value = false
}
async function confirmChange() {
  if (busy.value || pendingTask.value || !online.value || !parent.value.trim()) return
  validation.value = null
  confirming.value = true
  const signal = controller.signal
  try {
    const result = await run({ operation: 'validate_destination', parent: parent.value.trim() })
    if (result?.status === 'success') { validation.value = result; confirmOpen.value = true }
  } finally {
    if (!signal.aborted) confirming.value = false
  }
}
async function relocate() {
  const evidence = validation.value
  if (!evidence || busy.value) return
  confirmOpen.value = false
  const result = await run({ operation: 'relocate', validation_task_id: evidence.id })
  validation.value = null
  if (result?.status === 'success') cancelDirectoryEditor()
}
watch(parent, () => { validation.value = null; confirmOpen.value = false; clearDirectoryError() })
watch(() => [props.node.id, props.active] as const, async ([id, active], previous) => {
  if (previous && previous[0] === id && previous[1] === active) return
  controller.abort()
  controller = new AbortController()
  busy.value = false
  confirming.value = false
  confirmOpen.value = false
  validation.value = null
  if (id !== previous?.[0]) {
    snapshot.value = null; task.value = null; policySnapshot.value = null; retryPolicy.value = false
    blocking.value = '10'; parent.value = ''; changeDirectoryOpen.value = false; error.value = ''; errorOperation.value = ''
  }
  if (!active) return
  const signal = controller.signal
  try {
    await reload(signal)
    if (signal.aborted || !supported.value || !online.value) return
    if (pendingTask.value && task.value) {
      // Reattach to an existing operation, never create a duplicate on reopening.
      await run({}, task.value)
    } else {
      // One real Agent measurement per Advanced-tab entry; no periodic sampling.
      await run({ operation: 'refresh' })
    }
  } catch (cause) {
    if (!signal.aborted) error.value = apiErrorMessageI18n(cause, t, t('nodeCache.operationFailed'))
  }
}, { immediate: true })
onBeforeUnmount(() => controller.abort())
defineExpose({ cachePolicyDirty, cachePolicyError, cacheOperationPending, saveCachePolicy, cancelCacheEdits })
</script>

<template>
  <section class="hfl-detail-section node-cache-panel">
    <h4 class="hfl-detail-section__title">
      {{ t('nodeCache.title') }}
    </h4>
    <p
      v-if="!loaded"
      class="node-cache-loading"
      role="status"
    >
      {{ t('nodeCache.loading') }}
    </p>
    <template v-else-if="!supported">
      <ElAlert
        :closable="false"
        type="warning"
        show-icon
        class="node-cache-capability-alert"
      >
        <p class="node-cache-alert-copy">
          {{ t(capability === 'unknown' ? 'nodeCache.unknown' : 'nodeCache.legacy') }}
        </p>
        <p class="node-cache-alert-copy">
          {{ t('nodeCache.upgradeHelp') }}
        </p>
      </ElAlert>
      <slot name="legacy-settings" />
    </template>
    <template v-else>
      <div class="node-cache-groups">
        <div class="node-cache-group node-cache-summary">
          <div class="node-cache-root-row">
            <div class="node-cache-group__heading">
              <div class="node-cache-group__title">
                {{ t('nodeCache.activeRoot') }}
              </div>
              <ElTooltip
                :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))' }"
                placement="top"
                trigger="hover"
                :show-after="150"
              >
                <template #content>
                  <div class="node-cache-threshold-help node-cache-directory-help">
                    <p>{{ t('nodeCache.directoryHelp.purpose') }}</p>
                    <p class="node-cache-threshold-help__example">
                      {{ t('nodeCache.directoryHelp.heading') }}
                    </p>
                    <ul>
                      <li>{{ t('nodeCache.directoryHelp.backup') }}</li>
                      <li>{{ t('nodeCache.directoryHelp.disk') }}</li>
                      <li>{{ t('nodeCache.directoryHelp.relocate') }}</li>
                    </ul>
                    <p>{{ t('nodeCache.directoryHelp.note') }}</p>
                  </div>
                </template>
                <button
                  type="button"
                  class="node-cache-help node-cache-directory-help-trigger"
                  :aria-label="t('nodeCache.activeRoot')"
                >
                  <Info
                    :size="14"
                    aria-hidden="true"
                  />
                </button>
              </ElTooltip>
            </div>
            <ElButton
              plain
              :disabled="saving || busy || pendingTask || !online"
              @click="openDirectoryEditor"
            >
              {{ t('nodeCache.changeDirectory') }}
            </ElButton>
          </div>
          <div class="node-cache-group__body node-cache-root">
            <code class="node-cache-path">{{ report?.active_cache_root || '—' }}</code>
            <div
              v-if="changeDirectoryOpen"
              class="node-cache-directory-editor"
            >
              <label for="cache-destination">{{ t('nodeCache.destinationLabel') }}</label>
              <div class="node-cache-directory-editor__row">
                <ElInput
                  id="cache-destination"
                  v-model="parent"
                  :disabled="saving || busy || pendingTask"
                  :placeholder="t('nodeCache.destination')"
                  :aria-invalid="directoryError ? 'true' : undefined"
                  :aria-describedby="directoryError ? 'cache-destination-error' : undefined"
                />
                <ElButton
                  text
                  :disabled="saving || busy || pendingTask"
                  @click="cancelDirectoryEditor"
                >
                  {{ t('nodeCache.cancelChange') }}
                </ElButton>
                <ElButton
                  type="primary"
                  :loading="confirming || (busy && task?.kind === 'cache.relocate')"
                  :disabled="saving || !online || !parent.trim() || pendingTask || busy"
                  @click="confirmChange"
                >
                  {{ t(confirming || (busy && task?.kind === 'cache.relocate') ? 'nodeCache.validatingChange' : 'nodeCache.confirmChange') }}
                </ElButton>
              </div>
              <ElAlert
                v-if="directoryError"
                id="cache-destination-error"
                type="error"
                :title="t('nodeCache.destinationValidationFailed')"
                :closable="false"
                show-icon
              >
                {{ cacheErrorText(directoryError) }}
              </ElAlert>
              <p class="hfl-detail-config-cell__hint">
                {{ t('nodeCache.destinationHelp') }}
              </p>
            </div>
            <ElAlert
              v-if="visibleRelocation"
              class="node-cache-relocation-status"
              :type="relocationAlertType"
              :title="t(`nodeCache.stages.${visibleRelocation.stage || 'waiting_for_users'}`)"
              :closable="false"
              show-icon
              role="status"
            >
              <div class="node-cache-relocation-status__body">
                <p>{{ relocationHelp }}</p>
                <div v-if="visibleRelocation.source">
                  <span>{{ t('nodeCache.relocationSource') }}:</span>
                  <code class="node-cache-path">{{ visibleRelocation.source }}</code>
                </div>
                <div v-if="visibleRelocation.destination">
                  <span>{{ t('nodeCache.destinationLabel') }}:</span>
                  <code class="node-cache-path">{{ visibleRelocation.destination }}</code>
                </div>
                <p v-if="visibleRelocation.error">
                  {{ cacheErrorText(visibleRelocation.error) }}
                </p>
                <details
                  v-if="visibleRelocation.error && cacheErrorText(visibleRelocation.error) !== visibleRelocation.error"
                  class="node-cache-technical"
                >
                  <summary>{{ t('ops.task.failureDetails.technicalDetails') }}</summary>
                  <pre>{{ visibleRelocation.error }}</pre>
                </details>
                <ElButton
                  v-if="visibleRelocation.stage === 'switched_cleanup_pending'"
                  :disabled="saving || busy || pendingTask || !online"
                  @click="run({ operation: 'retry_cleanup' })"
                >
                  {{ t('nodeCache.retryCleanup') }}
                </ElButton>
              </div>
            </ElAlert>
            <dl class="node-cache-space-lines">
              <div><dt>{{ t('nodeCache.usage') }}:</dt><dd>{{ bytes(report?.usage?.logical_bytes) }}</dd></div>
              <div><dt>{{ t('nodeCache.available') }}:</dt><dd>{{ bytes(report?.space?.available_bytes) }}</dd></div>
              <div>
                <dt>{{ t('nodeCache.lastChecked') }}:</dt>
                <dd class="node-cache-last-check">
                  <span>{{ timestamp(checkedAt) }}</span>
                  <ElButton
                    class="node-cache-refresh"
                    text
                    circle
                    size="small"
                    :icon="RefreshCw"
                    :aria-label="t('nodeCache.refresh')"
                    :title="t('nodeCache.refresh')"
                    :loading="busy && task?.kind === 'cache.status'"
                    :disabled="saving || busy || pendingTask || !online"
                    @click="run({ operation: 'refresh' })"
                  />
                </dd>
              </div>
            </dl>
            <p
              v-if="report?.usage && !report.usage.complete"
              class="hfl-detail-config-cell__hint"
            >
              {{ t('nodeCache.partial') }}
            </p>
            <p
              v-if="report?.space_error"
              class="node-cache-error"
              role="alert"
            >
              {{ report.space_error }}
            </p>
            <p
              v-if="!online"
              class="hfl-detail-config-cell__hint"
            >
              {{ t('nodeCache.offline') }}
            </p>
            <p
              v-if="pendingTask && ['cache.relocate', 'cache.relocation.retry'].includes(task?.kind || '')"
              role="status"
            >
              {{ t('nodeCache.operationPending') }}
            </p>
          </div>
        </div>
        <div class="node-cache-group node-cache-policy node-cache-blocking-group">
          <div class="node-cache-group__heading">
            <label
              class="node-cache-group__title"
              for="cache-blocking"
            >{{ t('nodeCache.blocking') }}</label>
            <ElTooltip
              :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))' }"
              placement="top"
              trigger="hover"
              :show-after="150"
            >
              <template #content>
                <div class="node-cache-threshold-help">
                  <p>{{ t('nodeCache.blockingHelp.purpose') }}</p>
                  <p class="node-cache-threshold-help__example">
                    {{ t('nodeCache.blockingHelp.example') }}
                  </p>
                  <ul>
                    <li>{{ t('nodeCache.blockingHelp.blocked') }}</li>
                    <li>{{ t('nodeCache.blockingHelp.warning') }}</li>
                    <li>{{ t('nodeCache.blockingHelp.normal') }}</li>
                  </ul>
                  <p>{{ t('nodeCache.blockingHelp.note') }}</p>
                </div>
              </template>
              <button
                type="button"
                class="node-cache-help"
                :aria-label="t('nodeCache.blocking')"
              >
                <Info
                  :size="14"
                  aria-hidden="true"
                />
              </button>
            </ElTooltip>
          </div>
          <div class="node-cache-group__body node-cache-policy-body">
            <div class="hfl-detail-form-input node-cache-blocking-input">
              <div
                class="el-input-number is-controls-right is-left hfl-detail-form-input__num"
                :class="{ 'is-disabled': thresholdEditingBlocked }"
              >
                <button
                  type="button"
                  class="el-input-number__increase node-cache-step node-cache-step--increase"
                  :class="{ 'is-disabled': increaseBlocked }"
                  :disabled="increaseBlocked"
                  :aria-label="componentText('el.inputNumber.increase')"
                  @mousedown.prevent
                  @click="stepBlocking(1)"
                >
                  <ElIcon><ChevronUp /></ElIcon>
                </button>
                <button
                  type="button"
                  class="el-input-number__decrease node-cache-step"
                  :class="{ 'is-disabled': decreaseBlocked }"
                  :disabled="decreaseBlocked"
                  :aria-label="componentText('el.inputNumber.decrease')"
                  @mousedown.prevent
                  @click="stepBlocking(-1)"
                >
                  <ElIcon><ChevronDown /></ElIcon>
                </button>
                <ElInput
                  id="cache-blocking"
                  v-model="blocking"
                  :disabled="thresholdEditingBlocked"
                  inputmode="decimal"
                  container-role="spinbutton"
                  :aria-valuenow="blockingBytes == null ? undefined : blocking"
                  :aria-valuemin="1 / GIB"
                  :aria-valuemax="maxBlockingBytes / GIB"
                  :aria-invalid="Boolean(cachePolicyError)"
                  @keydown.up.prevent="stepBlocking(1)"
                  @keydown.down.prevent="stepBlocking(-1)"
                />
              </div>
              <div class="hfl-detail-form-input__suffix">
                GiB
              </div>
            </div>
            <p
              v-if="cachePolicyError"
              class="node-cache-error"
              role="alert"
            >
              {{ cachePolicyError }}
            </p>
            <p
              v-else-if="policyWaitNotice"
              class="hfl-detail-row__hint"
              role="status"
            >
              {{ policyWaitNotice }}
            </p>
          </div>
        </div>
        <div class="node-cache-group node-cache-warning-group">
          <div class="node-cache-group__heading">
            <span class="node-cache-group__title">{{ t('nodeCache.warning') }}</span>
            <ElTooltip
              :popper-style="{ maxWidth: 'min(360px, calc(100vw - 32px))' }"
              placement="top"
              trigger="hover"
              :show-after="150"
            >
              <template #content>
                <div class="node-cache-threshold-help node-cache-warning-help">
                  <p>{{ t('nodeCache.warningHelp.purpose') }}</p>
                  <p>{{ t('nodeCache.warningHelp.formula') }}</p>
                  <p class="node-cache-threshold-help__example">
                    {{ t('nodeCache.warningHelp.example') }}
                  </p>
                  <ul>
                    <li>{{ t('nodeCache.warningHelp.blocked') }}</li>
                    <li>{{ t('nodeCache.warningHelp.warning') }}</li>
                    <li>{{ t('nodeCache.warningHelp.normal') }}</li>
                  </ul>
                  <p>{{ t('nodeCache.warningHelp.note') }}</p>
                </div>
              </template>
              <button
                type="button"
                class="node-cache-help node-cache-warning-help-trigger"
                :aria-label="t('nodeCache.warning')"
              >
                <Info
                  :size="14"
                  aria-hidden="true"
                />
              </button>
            </ElTooltip>
          </div>
          <p class="node-cache-group__body node-cache-warning-line">
            {{ bytes(warningBytes) }} ({{ t(binaryWarning ? 'nodeCache.warningFormula' : 'nodeCache.warningActual') }})
          </p>
        </div>
      </div>
    </template>
    <DangerConfirmDialog
      v-model="confirmOpen"
      :title="t('nodeCache.changeDirectory')"
      :message="t('nodeCache.confirmRelocation')"
      :items="[{ name: node.name, description: String(validation?.result?.destination || '') }]"
      :confirmation-context="`${node.id}:${validation?.id || ''}`"
      confirm-mode="keyword"
      confirm-keyword="MOVE CACHE"
      :confirm-text="t('nodeCache.confirmChange')"
      :confirm-disabled="!validation || busy"
      @confirm="relocate"
      @cancel="confirmOpen = false"
    />
    <ElAlert
      v-if="generalError"
      class="node-cache-general-error"
      :title="cacheErrorText(generalError)"
      type="error"
      :closable="false"
      show-icon
    />
  </section>
</template>

<style scoped>
.node-cache-blocking-input {
  width: 300px;
}

.node-cache-step {
  padding: 0;
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
}

.node-cache-step--increase {
  border-bottom: var(--el-border);
}

.node-cache-step:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}

.node-cache-panel { display: grid; gap: 12px; }
.node-cache-panel > :deep(.el-alert) { width: auto; margin: 0 16px 12px; }
.node-cache-loading { margin: 16px; color: var(--el-text-color-secondary); }
.node-cache-alert-copy { margin: 0; font-size: inherit; line-height: 1.5; }
.node-cache-alert-copy + .node-cache-alert-copy { margin-top: 4px; }
.node-cache-groups { display: grid; gap: 24px; padding: 4px 16px 16px; font-size: var(--el-font-size-base, 14px); }
.node-cache-group { display: grid; gap: 12px; min-width: 0; }
.node-cache-group__title { margin: 0; color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; line-height: 1.5; }
.node-cache-group__heading { display: flex; align-items: center; gap: 6px; }
.node-cache-group > .node-cache-group__body { min-width: 0; margin: 0; margin-inline-start: 16px; }
.node-cache-root-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.node-cache-root { display: grid; gap: 14px; }
.node-cache-root > p { margin: 0; }
.node-cache-path { overflow-wrap: anywhere; display: block; line-height: 1.5; }
.node-cache-space-lines { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 16px; margin: 0; }
.node-cache-space-lines > div { display: contents; }
.node-cache-space-lines dt { font-weight: 400; }
.node-cache-space-lines dd { margin: 0; font-weight: 400; overflow-wrap: anywhere; }
.node-cache-policy-body { display: grid; gap: 8px; }
.node-cache-threshold-help { max-width: 100%; line-height: 1.6; white-space: normal; overflow-wrap: anywhere; }
.node-cache-threshold-help p { margin: 0; }
.node-cache-threshold-help p.node-cache-threshold-help__example { margin-top: 10px; font-weight: 600; }
.node-cache-threshold-help ul { display: grid; gap: 4px; margin: 6px 0 10px; padding-left: 18px; list-style-type: disc; }
.node-cache-help { display: inline-flex; padding: 0; border: 0; background: none; color: var(--el-color-primary); cursor: help; }
.node-cache-warning-line { margin: 0; }
.node-cache-directory-editor { display: grid; gap: 8px; }
.node-cache-directory-editor__row { display: flex; align-items: center; gap: 8px; }
.node-cache-directory-editor__row > .el-input { flex: 1; min-width: 0; }
.node-cache-directory-editor > p, .node-cache-summary > p { margin: 0; }
.node-cache-last-check { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.node-cache-refresh { flex: 0 0 auto; margin: 0; }
.node-cache-relocation-status { align-items: flex-start; }
.node-cache-relocation-status :deep(.el-alert__content) { min-width: 0; flex: 1; }
.node-cache-relocation-status__body { display: grid; gap: 8px; }
.node-cache-relocation-status__body p { margin: 0; }
.node-cache-relocation-status__body > .el-button { justify-self: start; }
.node-cache-technical pre { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.node-cache-error { margin: 0; color: var(--el-color-danger); overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .node-cache-root-row, .node-cache-directory-editor__row { flex-wrap: wrap; }
  .node-cache-directory-editor__row > .el-input { flex-basis: 100%; }
  .node-cache-space-lines { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .node-cache-space-lines dd { margin-bottom: 8px; }
}
</style>
