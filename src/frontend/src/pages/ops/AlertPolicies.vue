<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ChevronDown,
  Check,
  CirclePlay,
  CircleStop,
  Copy,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  SquarePen,
  Trash2,
  X,
} from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import AlertPolicyTypeLabel from '../../components/AlertPolicyTypeLabel.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useDebouncedAction } from '../../composables/useDebouncedAction'
import { useAlertPolicyLabels } from '../../composables/useAlertPolicyLabels'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { formatLocalDateTime } from '../../lib/dateTime'
import { booleanStatusTag, severityStatusTagAttrs } from '../../lib/statusTag'
import { apiErrorMessageI18n } from '../../lib/api'
import {
  bulkDeletePolicies,
  bulkStatePolicies,
  deletePolicy,
  duplicatePolicy,
  getPolicy,
  listPolicies,
  policyStatistics,
  updatePolicy,
  type AlertPolicy,
} from '../../lib/alertApi'
import DangerConfirmDialog, {
  type DangerConfirmItem,
} from '../../components/DangerConfirmDialog.vue'

type PolicyInlineField = 'name' | 'description' | 'severity' | 'enabled'

const router = useRouter()
const { t } = useI18n()
const opsMenus = useOpsMenus()
const { policyTypeLabel, resourceTypeLabel, policyTypeOptions } = useAlertPolicyLabels()
const { drawerSize, bindDrawerResize } = useResponsiveDrawerWidth()
const { schedule: scheduleFilterSearch, runNow: runFilterSearch } = useDebouncedAction(() => applyFilters())

const loading = ref(false)
const detailsLoading = ref(false)
const detailsError = ref<string | null>(null)
const showDetails = ref(false)
const detailPolicy = ref<AlertPolicy | null>(null)
const policyInlineEditing = ref<PolicyInlineField | null>(null)
const policyInlineDraft = ref<string | boolean>('')
const policyInlineSaving = ref(false)
const batchActionLoading = ref(false)
const moreActionsOpen = ref(false)
const alertPoliciesTableRef = ref()
const policies = ref<AlertPolicy[]>([])
const selectedPolicies = ref<AlertPolicy[]>([])
const skipNextSearchWatch = ref(false)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const filters = reactive({ search: '', type: '', severity: '', enabled: '' })

const stats = ref({ total: 0, enabled: 0, critical: 0 })
const hasActiveFilters = computed(() => (
  Boolean(filters.search.trim())
  || Boolean(filters.type)
  || Boolean(filters.severity)
  || Boolean(filters.enabled)
))
const emptyDescription = computed(() => (
  hasActiveFilters.value ? t('ops.alerts.rules.emptyFiltered') : t('ops.alerts.rules.emptyNoRules')
))
const hasSelectedPolicies = computed(() => selectedPolicies.value.length > 0)
const moreEditDisabled = computed(() => selectedPolicies.value.length !== 1 || batchActionLoading.value)
const moreBatchDisabled = computed(() => !selectedPolicies.value.length || batchActionLoading.value)
const batchEnableDisabled = computed(() => batchActionLoading.value || !selectedPolicies.value.some((row) => !row.enabled))
const batchDisableDisabled = computed(() => batchActionLoading.value || !selectedPolicies.value.some((row) => row.enabled))

type PolicyDangerKind = 'single-delete' | 'batch-enable' | 'batch-disable' | 'batch-delete'

type PendingPolicyDangerAction = {
  kind: PolicyDangerKind
  rows: AlertPolicy[]
  title: string
  subtitle: string
  message: string
  warning: string
  irreversible: string
  items: DangerConfirmItem[]
  keyword: string
  keywordHint: string
  confirmText: string
  level: 'medium' | 'high'
}

const pendingDangerAction = ref<PendingPolicyDangerAction | null>(null)
const dangerDialogOpen = ref(false)
const dangerDialogLoading = ref(false)
const dangerDialogSource = ref<'row' | 'drawer' | 'batch' | null>(null)

function channelLabel(policy: AlertPolicy) {
  const list = policy.notificationChannels || policy.notification_channels || []
  if (!list.length) return t('ops.alertsCenter.common.noChannelSelected')
  return list.map((c) => c.name).join(', ')
}

function detailChannels(policy: AlertPolicy | null) {
  return policy?.notificationChannels || policy?.notification_channels || []
}

function detailResourceIds(policy: AlertPolicy | null) {
  return policy?.resourceIds || policy?.resource_ids || []
}

function detailResourceType(policy: AlertPolicy | null) {
  return policy?.resourceType || policy?.resource_type || null
}

function detailTriggerRule(policy: AlertPolicy | null) {
  return policy?.triggerRule || policy?.trigger_rule || {}
}

function detailRecoveryRule(policy: AlertPolicy | null) {
  return policy?.recoveryRule || policy?.recovery_rule || {}
}

function alertPolicyNameMeta(policy: AlertPolicy) {
  return policy.description?.trim() || ''
}

function formatJson(value: unknown) {
  if (!value || (typeof value === 'object' && Object.keys(value as Record<string, unknown>).length === 0)) return '—'
  return JSON.stringify(value, null, 2)
}

function hasJsonValue(value: unknown) {
  return Boolean(value && (typeof value !== 'object' || Object.keys(value as Record<string, unknown>).length > 0))
}

function policyInlineValue(policy: AlertPolicy, field: PolicyInlineField): string | boolean {
  if (field === 'name') return policy.name || ''
  if (field === 'description') return policy.description || ''
  if (field === 'severity') return policy.severity || 'warning'
  if (field === 'enabled') return Boolean(policy.enabled)
  return ''
}

async function loadPolicies() {
  loading.value = true
  try {
    const [res, statRes] = await Promise.all([
      listPolicies({
        page: pagination.page,
        page_size: pagination.pageSize,
        search: filters.search,
        type: filters.type,
        severity: filters.severity,
        enabled: filters.enabled,
      }),
      policyStatistics({
        search: filters.search,
        type: filters.type,
        severity: filters.severity,
        enabled: filters.enabled,
      }),
    ])
    policies.value = res.results
    pagination.count = res.count
    stats.value = {
      total: statRes.total,
      enabled: statRes.enabled,
      critical: statRes.critical,
    }
  } finally {
    loading.value = false
  }
}

function openCreate() {
  router.push('/ops/alerts/rules/create')
}

function openEdit(row: AlertPolicy) {
  router.push(`/ops/alerts/rules/${row.id}/edit`)
}

async function openDetails(row: AlertPolicy) {
  detailPolicy.value = row
  detailsError.value = null
  detailsLoading.value = true
  showDetails.value = true
  try {
    detailPolicy.value = await getPolicy(row.id)
  } catch (e) {
    detailsError.value = apiErrorMessageI18n(e, t)
  } finally {
    detailsLoading.value = false
  }
}

function closeDetails() {
  showDetails.value = false
  detailsError.value = null
  cancelPolicyInlineEdit()
}

async function retryLoadDetails() {
  const row = detailPolicy.value
  if (row) await openDetails(row)
}

function isPolicyInlineEditing(field: PolicyInlineField) {
  return policyInlineEditing.value === field
}

function beginPolicyInlineEdit(field: PolicyInlineField) {
  if (!detailPolicy.value || policyInlineSaving.value) return
  policyInlineEditing.value = field
  policyInlineDraft.value = policyInlineValue(detailPolicy.value, field)
}

function cancelPolicyInlineEdit() {
  policyInlineEditing.value = null
  policyInlineDraft.value = ''
}

async function savePolicyInlineEdit() {
  const row = detailPolicy.value
  const field = policyInlineEditing.value
  if (!row || !field || policyInlineSaving.value) return

  const payload: Record<string, unknown> = {}
  if (field === 'name') {
    const name = String(policyInlineDraft.value).trim()
    if (!name) {
      ElMessage.warning({ message: t('ops.alerts.rules.validateNameRequired'), grouping: true })
      return
    }
    payload.name = name
  } else if (field === 'description') {
    payload.description = String(policyInlineDraft.value).trim()
  } else if (field === 'severity') {
    payload.severity = String(policyInlineDraft.value)
  } else if (field === 'enabled') {
    payload.enabled = Boolean(policyInlineDraft.value)
  }

  policyInlineSaving.value = true
  try {
    detailPolicy.value = await updatePolicy(row.id, payload)
    ElMessage.success({ message: t('ops.alerts.rules.msgUpdated'), grouping: true })
    cancelPolicyInlineEdit()
    await loadPolicies()
  } finally {
    policyInlineSaving.value = false
  }
}


async function copyPolicy(row: AlertPolicy) {
  await duplicatePolicy(row.id)
  ElMessage.success({ message: t('ops.alertsCenter.common.duplicate'), grouping: true })
  await loadPolicies()
}

function buildPolicyDangerItems(rows: AlertPolicy[]): DangerConfirmItem[] {
  return rows.map((row) => ({
    key: row.id,
    name: row.name,
    description: `${policyTypeLabel(row.type)} · ${severityLabel(row.severity)}`,
    status: {
      label: row.enabled
        ? t('ops.alertsCenter.common.enabled')
        : t('ops.alertsCenter.common.disabled'),
      tone: row.enabled ? 'success' : 'neutral',
    },
  }))
}

function policyBatchSkipNotice(kind: PolicyDangerKind, selectedCount: number, changeCount: number) {
  const skipped = Math.max(0, selectedCount - changeCount)
  if (!skipped || (kind !== 'batch-enable' && kind !== 'batch-disable')) return ''
  return t(
    kind === 'batch-enable'
      ? 'ops.alertsCenter.common.batchEnableSkipNotice'
      : 'ops.alertsCenter.common.batchDisableSkipNotice',
    { selected: selectedCount, changed: changeCount, skipped },
  )
}

function buildPolicyDangerAction(
  kind: PolicyDangerKind,
  rows: AlertPolicy[],
  selectedCount = rows.length,
): PendingPolicyDangerAction {
  const count = rows.length
  const isDelete = kind === 'single-delete' || kind === 'batch-delete'
  const isBatch = kind !== 'single-delete'
  const keyword = isDelete
    ? t('ops.alertsCenter.common.deleteKeyword')
    : kind === 'batch-disable'
      ? t('ops.alertsCenter.common.disableKeyword')
      : t('ops.alertsCenter.common.enableKeyword')

  const titleKey = {
    'single-delete': 'ops.alertsCenter.common.confirmDeleteTitle',
    'batch-enable': 'ops.alertsCenter.common.confirmBatchEnableTitle',
    'batch-disable': 'ops.alertsCenter.common.confirmBatchDisableTitle',
    'batch-delete': 'ops.alertsCenter.common.confirmBatchDeleteTitle',
  }[kind]
  const messageKey = {
    'single-delete': 'ops.alertsCenter.common.confirmDeleteMessage',
    'batch-enable': 'ops.alertsCenter.common.confirmBatchEnableMessage',
    'batch-disable': 'ops.alertsCenter.common.confirmBatchDisableMessage',
    'batch-delete': 'ops.alertsCenter.common.confirmBatchDeleteMessage',
  }[kind]
  const warningKey = {
    'single-delete': 'ops.alertsCenter.common.confirmDeleteWarning',
    'batch-enable': 'ops.alertsCenter.common.confirmBatchEnableWarning',
    'batch-disable': 'ops.alertsCenter.common.confirmBatchDisableWarning',
    'batch-delete': 'ops.alertsCenter.common.confirmBatchDeleteWarning',
  }[kind]
  const hintKey = {
    'single-delete': 'ops.alertsCenter.common.deleteKeywordHint',
    'batch-enable': 'ops.alertsCenter.common.enableKeywordHint',
    'batch-disable': 'ops.alertsCenter.common.disableKeywordHint',
    'batch-delete': 'ops.alertsCenter.common.batchDeleteKeywordHint',
  }[kind]
  const confirmTextKey = {
    'single-delete': 'ops.alertsCenter.common.deletePolicy',
    'batch-enable': 'ops.alertsCenter.common.confirmEnableCount',
    'batch-disable': 'ops.alertsCenter.common.confirmDisableCount',
    'batch-delete': 'ops.alertsCenter.common.confirmDeleteCount',
  }[kind]

  const baseMessage = t(messageKey, { name: rows[0]?.name ?? '', n: count })
  const skipNotice = policyBatchSkipNotice(kind, selectedCount, count)

  return {
    kind,
    rows,
    title: t(titleKey, { name: rows[0]?.name ?? '', n: count }),
    subtitle: isBatch
      ? t('ops.alertsCenter.common.batchPolicySubtitle', { n: count })
      : t('ops.alertsCenter.common.singlePolicySubtitle', { id: rows[0]?.id ?? '' }),
    message: skipNotice ? `${skipNotice}\n\n${baseMessage}` : baseMessage,
    warning: t(warningKey, { n: count }),
    irreversible: isDelete
      ? t('ops.alertsCenter.common.deleteIrreversible')
      : t('ops.alertsCenter.common.stateChangeIrreversible'),
    items: buildPolicyDangerItems(rows),
    keyword,
    keywordHint: t(hintKey, { name: rows[0]?.name ?? '', n: count }),
    confirmText: t(confirmTextKey, { n: count }),
    level: isDelete ? 'high' : 'medium',
  }
}

function openPolicyDangerDialog(
  kind: PolicyDangerKind,
  rows: AlertPolicy[],
  source: 'row' | 'drawer' | 'batch',
  selectedCount = rows.length,
) {
  if (!rows.length) return
  pendingDangerAction.value = buildPolicyDangerAction(kind, rows, selectedCount)
  dangerDialogSource.value = source
  dangerDialogOpen.value = true
}

function closePolicyDangerDialog() {
  if (dangerDialogLoading.value) return
  dangerDialogOpen.value = false
  pendingDangerAction.value = null
  dangerDialogSource.value = null
}


function applyFilters() {
  pagination.page = 1
  loadPolicies()
}

function runSearchImmediately() {
  skipNextSearchWatch.value = true
  runFilterSearch()
}

function clearSearch() {
  runSearchImmediately()
}

function onPolicySelectionChange(rows: AlertPolicy[]) {
  selectedPolicies.value = rows
}

function clearPolicySelection() {
  alertPoliciesTableRef.value?.clearSelection?.()
  selectedPolicies.value = []
}

function onMoreEdit() {
  moreActionsOpen.value = false
  const row = selectedPolicies.value[0]
  if (row) openEdit(row)
}

async function onMoreDuplicate() {
  moreActionsOpen.value = false
  const row = selectedPolicies.value[0]
  if (row) await copyPolicy(row)
}

async function runBatchState(enabled: boolean) {
  const rows = selectedPolicies.value
  if (!rows.length) return
  moreActionsOpen.value = false
  const targets = rows.filter((row) => row.enabled !== enabled)
  if (!targets.length) {
    ElMessage.info({ message: t('ops.alertsCenter.common.batchNoStateChange'), grouping: true })
    return
  }

  openPolicyDangerDialog(enabled ? 'batch-enable' : 'batch-disable', targets, 'batch', rows.length)
}

async function runBatchDelete() {
  const rows = selectedPolicies.value
  if (!rows.length) return
  moreActionsOpen.value = false
  openPolicyDangerDialog('batch-delete', [...rows], 'batch')
}

async function executePolicyDangerAction() {
  const action = pendingDangerAction.value
  if (!action) return
  dangerDialogLoading.value = true
  batchActionLoading.value = true
  let ok = 0
  let fail = 0
  try {
    if (action.kind === 'single-delete') {
      await deletePolicy(action.rows[0].id)
      ok = 1
    } else if (action.kind === 'batch-delete') {
      const res = await bulkDeletePolicies(action.rows.map((row) => row.id))
      ok = Array.isArray(res.deleted) ? res.deleted.length : 0
      fail = Array.isArray(res.failed) ? res.failed.length : 0
    } else {
      const res = await bulkStatePolicies(
        action.rows.map((row) => row.id),
        action.kind === 'batch-enable',
      )
      ok = Array.isArray(res.updated) ? res.updated.length : 0
      fail = Array.isArray(res.failed) ? res.failed.length : 0
    }
  } catch (e: unknown) {
    const fallbackKey = action.kind === 'single-delete'
      ? 'ops.alertsCenter.common.batchDeleteFailed'
      : action.kind === 'batch-delete'
      ? 'ops.alertsCenter.common.batchDeleteFailed'
      : action.kind === 'batch-disable'
        ? 'ops.alertsCenter.common.batchDisableFailed'
        : 'ops.alertsCenter.common.batchEnableFailed'
    ElMessage.error({
      message: apiErrorMessageI18n(e, t, t(fallbackKey)),
      grouping: true,
    })
    return
  } finally {
    dangerDialogLoading.value = false
    batchActionLoading.value = false
  }

  dangerDialogOpen.value = false
  const source = dangerDialogSource.value
  pendingDangerAction.value = null
  dangerDialogSource.value = null
  if (source === 'drawer' && action.kind === 'single-delete' && fail === 0) {
    showDetails.value = false
    detailPolicy.value = null
  }
  if (source === 'batch') clearPolicySelection()
  await loadPolicies()

  if (action.kind === 'single-delete') {
    if (fail === 0) ElMessage.success({ message: t('ops.alerts.rules.msgDeleted'), grouping: true })
    return
  }

  const successKey = {
    'batch-enable': 'ops.alertsCenter.common.batchEnableSuccess',
    'batch-disable': 'ops.alertsCenter.common.batchDisableSuccess',
    'batch-delete': 'ops.alertsCenter.common.batchDeleteSuccess',
  }[action.kind]
  const partialKey = {
    'batch-enable': 'ops.alertsCenter.common.batchEnablePartial',
    'batch-disable': 'ops.alertsCenter.common.batchDisablePartial',
    'batch-delete': 'ops.alertsCenter.common.batchDeletePartial',
  }[action.kind]
  if (fail === 0) ElMessage.success({ message: t(successKey, { n: ok }), grouping: true })
  else ElMessage.warning({ message: t(partialKey, { ok, fail }), grouping: true })
}

function scopeTagType(scope: string): 'primary' | 'info' {
  return scope === 'selected' ? 'primary' : 'info'
}

function scopeTagClass(scope: string) {
  return scope === 'selected' ? 'hfl-tag--scope-selected' : undefined
}

function severityLabel(severity?: string | null) {
  if (!severity) return t('common.empty')
  const key = `ops.alertsCenter.common.${severity}`
  const translated = t(key)
  return translated !== key ? translated : severity
}

function scopeLabel(scope?: string | null) {
  if (scope === 'all') return t('ops.alertsCenter.editor.scopeAll')
  if (scope === 'selected') return t('ops.alertsCenter.editor.scopeSelected')
  return scope || t('common.empty')
}

function formatDate(value?: string) {
  return formatLocalDateTime(value, '—')
}

onMounted(loadPolicies)

watch(
  () => filters.search,
  () => {
    if (skipNextSearchWatch.value) {
      skipNextSearchWatch.value = false
      return
    }
    scheduleFilterSearch()
  },
)

watch(
  () => [filters.type, filters.severity, filters.enabled],
  () => runFilterSearch(),
)
</script>

<template>
  <ModulePage
    :title="t('ops.alertsCenter.policies.title')"
    :menus="opsMenus"
    body-fill
  >
    <div class="hfl-ops-page hfl-ops-page--fill">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--3">
        <OpsStatCard
          :label="t('ops.alertsCenter.common.policies')"
          :value="stats.total"
          accent="indigo"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.alertsCenter.common.enabled')"
          :value="stats.enabled"
          accent="green"
          accent-side="left"
          value-class="text-emerald-600"
        />
        <OpsStatCard
          :label="t('ops.alertsCenter.common.critical')"
          :value="stats.critical"
          accent="red"
          accent-side="left"
          value-class="text-red-600"
        />
      </div>

      <HflTablePanel fill>
        <template #toolbar>
          <el-button
            type="primary"
            :icon="Plus"
            @click="openCreate"
          >
            {{ t('common.add') }}
          </el-button>
          <el-dropdown
            trigger="click"
            @visible-change="moreActionsOpen = $event"
          >
            <el-button :loading="batchActionLoading">
              {{ t('ops.alertsCenter.common.moreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  :disabled="moreEditDisabled"
                  @click="onMoreEdit"
                >
                  <span class="el-dropdown-menu__item-content">
                    <SquarePen
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.alertsCenter.common.editPolicy') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  :disabled="moreEditDisabled"
                  @click="onMoreDuplicate"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Copy
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.alertsCenter.common.duplicatePolicy') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  divided
                  :disabled="batchEnableDisabled"
                  @click="runBatchState(true)"
                >
                  <span class="el-dropdown-menu__item-content">
                    <CirclePlay
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.alertsCenter.common.enablePolicy') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  :disabled="batchDisableDisabled"
                  @click="runBatchState(false)"
                >
                  <span class="el-dropdown-menu__item-content">
                    <CircleStop
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.alertsCenter.common.disablePolicy') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  divided
                  class="el-dropdown-menu__item--danger"
                  :disabled="moreBatchDisabled"
                  @click="runBatchDelete"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Trash2
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.alertsCenter.common.deletePolicy') }}</span>
                  </span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
        <template #toolbar-actions>
          <el-input
            v-model="filters.search"
            class="hfl-list-search"
            clearable
            :placeholder="t('ops.alertsCenter.policies.searchPlaceholder')"
            @clear="clearSearch"
          >
            <template #prefix>
              <Search class="h-4 w-4 opacity-60" />
            </template>
          </el-input>
          <el-select
            v-model="filters.type"
            clearable
            style="width: 130px"
            :placeholder="t('ops.alertsCenter.common.allTypes')"
          >
            <el-option
              v-for="opt in policyTypeOptions"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-select>
          <el-select
            v-model="filters.severity"
            clearable
            style="width: 130px"
            :placeholder="t('ops.alertsCenter.common.allSeverity')"
          >
            <el-option
              value="critical"
              :label="t('ops.alertsCenter.common.critical')"
            />
            <el-option
              value="warning"
              :label="t('ops.alertsCenter.common.warning')"
            />
            <el-option
              value="info"
              :label="t('ops.alertsCenter.common.info')"
            />
          </el-select>
          <el-select
            v-model="filters.enabled"
            clearable
            style="width: 110px"
            :placeholder="t('ops.alertsCenter.common.status')"
          >
            <el-option
              value="true"
              :label="t('ops.alertsCenter.common.enabled')"
            />
            <el-option
              value="false"
              :label="t('ops.alertsCenter.common.disabled')"
            />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button
            class="hfl-refresh-button"
            :title="t('ops.task.btnRefresh')"
            :aria-label="t('ops.task.btnRefresh')"
            :disabled="loading"
            @click="loadPolicies"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </el-button>
        </template>

        <template #table="{ tableMaxHeight }">
          <el-table
            ref="alertPoliciesTableRef"
            v-table-column-resize="'ops.alertPolicies'"
            v-loading="loading"
            :data="policies"
            stripe
            class="hfl-list-table hfl-alert-policy-table w-full"
            row-key="id"
            :max-height="tableMaxHeight"
            @selection-change="onPolicySelectionChange"
          >
            <el-table-column
              type="selection"
              width="35"
              fixed="left"
            />
            <el-table-column
              :label="t('ops.alertsCenter.common.name')"
              min-width="220"
              fixed="left"
            >
              <template #default="{ row }">
                <div class="hfl-ops-primary-cell">
                  <div class="min-w-0">
                    <div class="flex min-w-0 items-center gap-1">
                      <button
                        type="button"
                        class="hfl-table-name-link hfl-table-name-link--single min-w-0"
                        @click="openDetails(row)"
                      >
                        {{ row.name }}
                      </button>
                    </div>
                    <div
                      v-if="alertPolicyNameMeta(row)"
                      class="hfl-ops-cell-stack__meta line-clamp-1"
                    >
                      {{ alertPolicyNameMeta(row) }}
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.common.type')"
              width="130"
            >
              <template #default="{ row }">
                <AlertPolicyTypeLabel :type="row.type" />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.policies.targetColumn')"
              width="130"
            >
              <template #default="{ row }">
                <span :class="{ 'hfl-empty-mark': !(row.resourceType || row.resource_type) }">{{ resourceTypeLabel(row.resourceType || row.resource_type) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.common.severity')"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  v-bind="severityStatusTagAttrs(row.severity)"
                  size="small"
                >
                  {{ severityLabel(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.policies.scope')"
              width="140"
            >
              <template #default="{ row }">
                <el-tag
                  :type="scopeTagType(row.scope)"
                  :class="scopeTagClass(row.scope)"
                  size="small"
                >
                  {{ scopeLabel(row.scope) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.common.status')"
              width="90"
            >
              <template #default="{ row }">
                <el-tag
                  :type="booleanStatusTag(row.enabled).type"
                  :class="booleanStatusTag(row.enabled).class"
                  size="small"
                >
                  {{ row.enabled ? t('ops.alertsCenter.common.enabled') : t('ops.alertsCenter.common.disabled') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.policies.notificationColumn')"
              min-width="160"
            >
              <template #default="{ row }">
                <span>{{ channelLabel(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.alertsCenter.common.updatedAt')"
              width="170"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !(row.updatedAt || row.updated_at) }"
                >
                  {{ formatDate(row.updatedAt || row.updated_at) }}
                </span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="emptyDescription" />
            </template>
          </el-table>
        </template>

        <template #footer>
          <span
            v-if="hasSelectedPolicies"
            class="hfl-list-footer__selected"
          >
            {{ t('ops.alertsCenter.common.selectedCount', { n: selectedPolicies.length }) }}
          </span>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="pagination.count"
            :page-sizes="[20, 30, 50, 100]"
            @current-change="loadPolicies"
            @size-change="() => { pagination.page = 1; loadPolicies() }"
          />
        </template>
      </HflTablePanel>
    </div>

    <ElDrawer
      v-model="showDetails"
      class="hfl-detail-drawer hfl-alert-policy-detail-drawer"
      :size="drawerSize"
      direction="rtl"
      @opened="bindDrawerResize"
      @closed="closeDetails"
    >
      <template #header>
        <span class="hfl-detail-drawer__title">
          {{ detailPolicy?.name || t('common.empty') }}
        </span>
      </template>

      <div class="hfl-detail-drawer__body">
        <div
          v-if="detailsLoading"
          class="hfl-alert-policy-detail-drawer__state"
        >
          {{ t('common.loading') }}
        </div>
        <div
          v-else-if="detailsError"
          class="hfl-alert-policy-detail-drawer__state"
        >
          <div class="hfl-alert-policy-detail-drawer__error">
            {{ detailsError }}
          </div>
          <ElButton
            size="small"
            type="primary"
            plain
            class="mt-2"
            @click="retryLoadDetails"
          >
            {{ t('common.retry') }}
          </ElButton>
        </div>
        <div
          v-else-if="detailPolicy"
          class="hfl-detail-sections"
        >
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.alertsCenter.common.basicInfo') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.id') }}</span>
                <span class="hfl-detail-row__value hfl-table-cell-mono">{{ detailPolicy.id }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.name') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--editable">
                  <template v-if="isPolicyInlineEditing('name')">
                    <ElInput
                      v-model="policyInlineDraft"
                      size="small"
                      class="hfl-detail-inline-edit__input"
                      @keyup.enter="savePolicyInlineEdit"
                    />
                    <span class="hfl-detail-inline-edit__actions">
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.save')"
                        :disabled="policyInlineSaving"
                        @click="savePolicyInlineEdit"
                      ><Check :size="14" /></ElButton>
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.cancel')"
                        :disabled="policyInlineSaving"
                        @click="cancelPolicyInlineEdit"
                      ><X :size="14" /></ElButton>
                    </span>
                  </template>
                  <template v-else>
                    <span class="hfl-detail-row__text">{{ detailPolicy.name }}</span>
                    <ElButton
                      text
                      circle
                      size="small"
                      class="hfl-detail-row__edit"
                      :title="t('common.edit')"
                      @click="beginPolicyInlineEdit('name')"
                    >
                      <Pencil :size="13" />
                    </ElButton>
                  </template>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.type') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !detailPolicy.type }"
                >{{ policyTypeLabel(detailPolicy.type) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.severity') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--editable">
                  <template v-if="isPolicyInlineEditing('severity')">
                    <ElSelect
                      v-model="policyInlineDraft"
                      size="small"
                      class="hfl-detail-inline-edit__input"
                    >
                      <ElOption
                        value="critical"
                        :label="t('ops.alertsCenter.common.critical')"
                      />
                      <ElOption
                        value="warning"
                        :label="t('ops.alertsCenter.common.warning')"
                      />
                      <ElOption
                        value="info"
                        :label="t('ops.alertsCenter.common.info')"
                      />
                    </ElSelect>
                    <span class="hfl-detail-inline-edit__actions">
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.save')"
                        :disabled="policyInlineSaving"
                        @click="savePolicyInlineEdit"
                      ><Check :size="14" /></ElButton>
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.cancel')"
                        :disabled="policyInlineSaving"
                        @click="cancelPolicyInlineEdit"
                      ><X :size="14" /></ElButton>
                    </span>
                  </template>
                  <template v-else>
                    <el-tag
                      v-bind="severityStatusTagAttrs(detailPolicy.severity)"
                      size="small"
                    >
                      {{ severityLabel(detailPolicy.severity) }}
                    </el-tag>
                    <ElButton
                      text
                      circle
                      size="small"
                      class="hfl-detail-row__edit"
                      :title="t('common.edit')"
                      @click="beginPolicyInlineEdit('severity')"
                    >
                      <Pencil :size="13" />
                    </ElButton>
                  </template>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.status') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--editable">
                  <template v-if="isPolicyInlineEditing('enabled')">
                    <ElSwitch v-model="policyInlineDraft" />
                    <span class="hfl-detail-inline-edit__actions">
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.save')"
                        :disabled="policyInlineSaving"
                        @click="savePolicyInlineEdit"
                      ><Check :size="14" /></ElButton>
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.cancel')"
                        :disabled="policyInlineSaving"
                        @click="cancelPolicyInlineEdit"
                      ><X :size="14" /></ElButton>
                    </span>
                  </template>
                  <template v-else>
                    <el-tag
                      :type="booleanStatusTag(detailPolicy.enabled).type"
                      :class="booleanStatusTag(detailPolicy.enabled).class"
                      size="small"
                    >
                      {{ detailPolicy.enabled ? t('ops.alertsCenter.common.enabled') : t('ops.alertsCenter.common.disabled') }}
                    </el-tag>
                    <ElButton
                      text
                      circle
                      size="small"
                      class="hfl-detail-row__edit"
                      :title="t('common.edit')"
                      @click="beginPolicyInlineEdit('enabled')"
                    >
                      <Pencil :size="13" />
                    </ElButton>
                  </template>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.scope') }}</span>
                <span class="hfl-detail-row__value">
                  <el-tag
                    :type="scopeTagType(detailPolicy.scope)"
                    :class="scopeTagClass(detailPolicy.scope)"
                    size="small"
                  >
                    {{ scopeLabel(detailPolicy.scope) }}
                  </el-tag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.targetColumn') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="{ 'hfl-detail-row__empty': !detailResourceType(detailPolicy) }"
                >{{ resourceTypeLabel(detailResourceType(detailPolicy)) }}</span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.updatedAt') }}</span>
                <span
                  class="hfl-detail-row__value hfl-table-cell-time"
                  :class="{ 'hfl-detail-row__empty': !(detailPolicy.updatedAt || detailPolicy.updated_at) }"
                >
                  {{ formatDate(detailPolicy.updatedAt || detailPolicy.updated_at) }}
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.description') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--break">
                  <template v-if="isPolicyInlineEditing('description')">
                    <ElInput
                      v-model="policyInlineDraft"
                      size="small"
                      type="textarea"
                      :rows="2"
                      class="hfl-detail-inline-edit__input"
                    />
                    <span class="hfl-detail-inline-edit__actions">
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.save')"
                        :disabled="policyInlineSaving"
                        @click="savePolicyInlineEdit"
                      ><Check :size="14" /></ElButton>
                      <ElButton
                        text
                        circle
                        size="small"
                        :title="t('common.cancel')"
                        :disabled="policyInlineSaving"
                        @click="cancelPolicyInlineEdit"
                      ><X :size="14" /></ElButton>
                    </span>
                  </template>
                  <template v-else>
                    <span
                      class="hfl-detail-row__text"
                      :class="{ 'hfl-empty-mark': !detailPolicy.description }"
                    >{{ detailPolicy.description || t('common.empty') }}</span>
                    <ElButton
                      text
                      circle
                      size="small"
                      class="hfl-detail-row__edit"
                      :title="t('common.edit')"
                      @click="beginPolicyInlineEdit('description')"
                    >
                      <Pencil :size="13" />
                    </ElButton>
                  </template>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.alertsCenter.common.resourcesAndChannels') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.editor.fieldMonitoringResources') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--break">
                  <span class="hfl-detail-row__text">
                    <template v-if="detailPolicy.scope === 'selected'">
                      {{ detailResourceIds(detailPolicy).length ? detailResourceIds(detailPolicy).join(', ') : t('common.empty') }}
                    </template>
                    <template v-else>
                      {{ t('ops.alertsCenter.editor.scopeAll') }}
                    </template>
                  </span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.policies.notificationColumn') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--break">
                  <span class="hfl-detail-row__text">
                    {{ detailChannels(detailPolicy).length ? detailChannels(detailPolicy).map((channel) => channel.name).join(', ') : t('ops.alertsCenter.common.noChannelSelected') }}
                  </span>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('ops.alertsCenter.common.ruleConfig') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.triggerRule') }}</span>
                <span class="hfl-detail-row__value hfl-alert-policy-detail-drawer__json-value">
                  <pre
                    v-if="hasJsonValue(detailTriggerRule(detailPolicy))"
                    class="hfl-alert-policy-detail-drawer__json"
                  >{{ formatJson(detailTriggerRule(detailPolicy)) }}</pre>
                  <span
                    v-else
                    class="hfl-empty-mark"
                  >{{ t('common.empty') }}</span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('ops.alertsCenter.common.recoveryRule') }}</span>
                <span class="hfl-detail-row__value hfl-alert-policy-detail-drawer__json-value">
                  <pre
                    v-if="hasJsonValue(detailRecoveryRule(detailPolicy))"
                    class="hfl-alert-policy-detail-drawer__json"
                  >{{ formatJson(detailRecoveryRule(detailPolicy)) }}</pre>
                  <span
                    v-else
                    class="hfl-empty-mark"
                  >{{ t('common.empty') }}</span>
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </ElDrawer>

    <DangerConfirmDialog
      v-if="pendingDangerAction"
      v-model="dangerDialogOpen"
      :title="pendingDangerAction.title"
      :subtitle="pendingDangerAction.subtitle"
      :message="pendingDangerAction.message"
      :warning="pendingDangerAction.warning"
      :items="pendingDangerAction.items"
      :items-heading="t('ops.alertsCenter.common.affectedPoliciesHeading', {
        n: pendingDangerAction.rows.length,
      })"
      :irreversible-hint="pendingDangerAction.irreversible"
      confirm-mode="keyword"
      :confirm-keyword="pendingDangerAction.keyword"
      :confirm-keyword-hint="pendingDangerAction.keywordHint"
      :confirm-keyword-placeholder="pendingDangerAction.keyword"
      :cancel-text="t('common.cancel')"
      :confirm-text="pendingDangerAction.confirmText"
      :loading="dangerDialogLoading"
      :level="pendingDangerAction.level"
      :width="pendingDangerAction.kind === 'single-delete' ? '560px' : '760px'"
      :irreversible-tone="pendingDangerAction.level === 'high' ? 'danger' : 'neutral'"
      @confirm="executePolicyDangerAction"
      @cancel="closePolicyDangerDialog"
    />
  </ModulePage>
</template>

<style scoped>
.hfl-alert-policy-table :deep(.el-table__body td.el-table__cell > .cell) {
  display: flex;
  min-height: 34px;
  align-items: center;
}

.hfl-alert-policy-detail-drawer__state {
  padding: 24px;
  color: #64748b;
  font-size: 13px;
}

.hfl-alert-policy-detail-drawer__error {
  color: var(--color-error);
}

.hfl-alert-policy-detail-drawer__json-value {
  align-items: stretch;
}

.hfl-alert-policy-detail-drawer__json {
  width: 100%;
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
