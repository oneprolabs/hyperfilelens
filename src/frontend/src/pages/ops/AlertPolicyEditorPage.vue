<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { ArrowLeft, BellRing, CircleAlert, Info, RefreshCw, TriangleAlert } from 'lucide-vue-next'
import {
  DEFAULT_ALERT_POLICY_TYPE,
  defaultResourceTypeFor,
  isAllowedAlertResourceType,
  useAlertPolicyLabels,
} from '../../composables/useAlertPolicyLabels'
import {
  createPolicy,
  fetchMetadata,
  fetchMetadataResources,
  getPolicy,
  updatePolicy,
} from '../../lib/alertApi'
import {
  defaultUnitForMetric,
  isByteMetric,
  metricsForResourceType,
  normalizeMetricCatalog,
  orderMetricKeys,
  unitsForMetric,
} from '../../lib/alertMetricCatalog'
import { listChannels } from '../../lib/notificationApi'
import { apiErrorMessageI18n } from '../../lib/api'
import { toApiError } from '../../lib/errors'
import {
  createPlatformAlertPolicy,
  fetchPlatformAlertMetadataMetrics,
  fetchPlatformAlertMetadataResources,
  fetchPlatformAlertPolicy,
  listPlatformNotificationChannelOptions,
  updatePlatformAlertPolicy,
} from '../../platform-ops/lib/platformOpsApi'
import {
  backendTriggerErrorSpecs,
  isValidTriggerValue,
  triggerSpecsForType,
  triggerValidationSpecs,
} from './alertPolicyValidation'
import { hasNumericRecovery, inverseOperator, recoveryKind, switchRecovery, switchTrigger } from './alertPolicyRecovery'
import { triggerSummary } from './alertTriggerPresentation'

export type AlertPolicyFormState = {
  name: string
  description: string
  type: string
  severity: string
  enabled: boolean
  resourceType: string
  scope: string
  resourceIds: string[]
  triggerRule: Record<string, unknown>
  recoveryRule: Record<string, unknown>
  notificationChannelIds: number[]
}

function defaultForm(): AlertPolicyFormState {
  return {
    name: '',
    description: '',
    type: DEFAULT_ALERT_POLICY_TYPE,
    severity: 'warning',
    enabled: true,
    resourceType: 'system',
    scope: 'all',
    resourceIds: [],
    triggerRule: {
      metric_key: 'cpu_usage',
      operator: '>=',
      threshold: 80,
      unit: '%',
      duration_seconds: 300,
      evaluation_interval_seconds: 60,
      repeat_interval_seconds: 3600,
      merge_enabled: false,
      merge_key: '',
      merge_window_seconds: 300,
    },
    recoveryRule: {
      enabled: true,
      condition: 'below_threshold',
      operator: '<',
      threshold: 70,
      duration_seconds: 180,
      notify_on_firing: true,
      notify_on_resolved: true,
    },
    notificationChannelIds: [],
  }
}

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const PREVIEW_EMPTY_VALUE = '—'

/** Admin Console Alerting reuses this editor for platform-owned alert rules. */
const isPlatformOps = computed(() => route.path.startsWith('/platform-ops/'))
const {
  policyTypeLabel,
  policyTypeOptions,
  policyTypeDescription,
  resourceTypeLabel,
  resourceTypeDescription,
  resourceTypeOptionsFor,
  metricLabel,
  healthCheckLabel,
  taskTypeLabel,
  taskEventLabel,
  eventCategoryLabel,
  eventTypeLabel,
} = useAlertPolicyLabels({
  platform: isPlatformOps,
})
const metricOptions = computed(() =>
  metricKeys.value.map((value) => ({
    value,
    label: metricLabel(value),
  })),
)
const healthCheckOptions = computed(() =>
  healthCheckTypes.value.map((value) => ({
    value,
    label: healthCheckLabel(value),
  })),
)
const taskTypeOptions = computed(() =>
  taskTypes.value.map((value) => ({
    value,
    label: taskTypeLabel(value),
  })),
)
const taskEventOptions = computed(() =>
  taskEventTypes.value.map((value) => ({
    value,
    label: taskEventLabel(value),
  })),
)
const eventCategoryOptions = computed(() =>
  eventCategories.value.map((value) => ({
    value,
    label: eventCategoryLabel(value),
  })),
)
const platformEventOptions = computed(() =>
  (eventTypesByCategory.value[eventCategory.value] || []).map((value) => ({
    value,
    label: eventTypeLabel(value),
  })),
)
function metricUnitDisplay(unit?: string | null): string {
  if (unit === '%') return t('ops.alertsCenter.editor.unitPercent')
  if (unit === 'B') return t('ops.alertsCenter.editor.unitBytes')
  if (unit === 'MiB') return t('ops.alertsCenter.editor.unitMib')
  if (unit === 'GiB') return t('ops.alertsCenter.editor.unitGib')
  if (unit === 'TiB') return t('ops.alertsCenter.editor.unitTib')
  if (!unit) return t('ops.alertsCenter.editor.unitNone')
  return unit
}

const metricUnitChoices = computed(() =>
  unitsForMetric(String(metricKey.value || '')).map((value) => ({
    value,
    label: metricUnitDisplay(value),
  })),
)
const metricUnitSelectable = computed(() =>
  isByteMetric(String(metricKey.value || '')),
)
const showMetricUnitField = computed(() => {
  if (form.type !== 'metric') return false
  // Load average / file count have no unit—hide the field instead of showing "None".
  return metricUnitSelectable.value || !!defaultUnitForMetric(String(metricKey.value || ''))
})
const metricUnitLabel = computed(() =>
  metricUnitDisplay(String(metricUnit.value ?? defaultUnitForMetric(String(metricKey.value || '')))),
)

function syncMetricUnit(metric = String(metricKey.value || '')) {
  const allowed = unitsForMetric(metric)
  if (!allowed.includes(String(metricUnit.value ?? ''))) {
    metricUnit.value = defaultUnitForMetric(metric)
  }
}
/** Per Alert Type: only applicable Resource Types, in canonical product order. */
const selectableResourceTypeOptions = computed(() => {
  const options = resourceTypeOptionsFor(form.type)
  if (
    form.resourceType
    && !options.some((opt) => opt.value === form.resourceType)
  ) {
    return [
      ...options,
      { value: form.resourceType, label: resourceTypeLabel(form.resourceType) },
    ]
  }
  return options
})
const listPath = computed(() => (
  isPlatformOps.value
    ? '/platform-ops/alert-center/policies'
    : '/ops/alerts/rules'
))

const saving = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const mainRef = ref<HTMLElement | null>(null)
const formRef = ref<FormInstance | null>(null)
const form = reactive(defaultForm())
// Preserve null/empty legacy settings when opening and saving an existing policy.
const loadedRecovery = ref<Record<string, unknown> | null>(null)
const loadedRecoverySnapshot = ref('')
const currentRecoveryKind = computed(() => recoveryKind(form.type, form.triggerRule))
const legacyNumericRecovery = computed(() =>
  currentRecoveryKind.value === 'numeric' && !hasNumericRecovery(form.recoveryRule),
)
const channels = ref<Array<{ id: number; name: string; type?: string; enabled?: boolean }>>([])
const editingId = computed(() => {
  const id = route.params.id
  return typeof id === 'string' && id ? id : null
})
const legacyTaskRecovery = computed(() =>
  form.type === 'task' && !!editingId.value && form.recoveryRule.enabled !== false,
)
const displayedAutoRecovery = computed(() =>
  form.recoveryRule.enabled !== false
  && (currentRecoveryKind.value !== 'manual' || legacyTaskRecovery.value),
)

const resourcesLoading = ref(false)
const hydratingPolicy = ref(false)
const resourceOptions = ref<Array<{ id: string; name: string; status?: string }>>([])
const metricKeys = ref<string[]>([])
const metricKeysLoading = ref(false)
const metricKeysError = ref(false)
const metricCatalogLoaded = ref(false)
let metricRequestId = 0
const metricConfigurationBlocked = computed(() =>
  form.type === 'metric' && (
    !metricCatalogLoaded.value || metricKeysLoading.value || metricKeysError.value
    || !metricKeys.value.includes(String(metricKey.value))
  ),
)
const metricCatalogHint = computed(() => {
  if (!metricCatalogLoaded.value || metricKeysLoading.value) return t('ops.alertsCenter.editor.metricLoading')
  if (metricKeysError.value) return t('ops.alertsCenter.editor.metricLoadFailed')
  if (!metricKeys.value.length) return t('ops.alertsCenter.editor.metricUnsupported')
  if (!metricKeys.value.includes(String(metricKey.value))) return t('ops.alertsCenter.editor.metricSelectAvailable')
  return ''
})
const metadataKinds = ['task-types', 'event-types', 'health-check-types'] as const
type MetadataKind = typeof metadataKinds[number]
const metadataState = reactive(Object.fromEntries(metadataKinds.map(kind => [
  kind, { status: 'loading' as 'loading' | 'ready' | 'error' | 'empty', request: 0 },
])) as Record<MetadataKind, { status: 'loading' | 'ready' | 'error' | 'empty'; request: number }>)
const requiredMetadataKinds = computed<MetadataKind[]>(() => {
  if (form.type === 'task') return ['task-types', 'event-types']
  if (form.type === 'event') return ['event-types']
  if (form.type === 'health' || form.type === 'availability') return ['health-check-types']
  return []
})
function metadataStatus(kind: MetadataKind) {
  const status = metadataState[kind].status
  if (kind === 'event-types' && status === 'ready') {
    if (form.type === 'task' && !taskEventTypes.value.length) return 'empty'
    if (form.type === 'event' && !eventCategories.value.length) return 'empty'
  }
  return status
}
const metadataSelectionValid = computed(() => {
  const rule = form.triggerRule
  if (form.type === 'task') return taskTypes.value.includes(String(rule.task_type))
    && taskEventTypes.value.includes(String(rule.event_type))
  if (form.type === 'health' || form.type === 'availability') {
    return healthCheckTypes.value.includes(String(rule.check_type))
  }
  if (form.type === 'event') {
    const selected = rule.event_types
    const allowed = eventTypesByCategory.value[String(rule.event_category)] || []
    // Empty selection is a normal create-time state; form rules validate on save.
    // Only reject values that are outside the loaded catalog.
    if (!eventCategories.value.includes(String(rule.event_category))) return false
    if (!Array.isArray(selected) || selected.length === 0) return true
    return selected.every(item => allowed.includes(String(item)))
  }
  return true
})
const metadataConfigurationBlocked = computed(() =>
  requiredMetadataKinds.value.some(kind => metadataStatus(kind) !== 'ready')
  || !metadataSelectionValid.value,
)
function metadataLabel(kind: MetadataKind) {
  const labels = {
    'task-types': 'taskType', 'event-types': form.type === 'task' ? 'eventType' : 'eventCategory',
    'health-check-types': 'checkType',
  }
  return t(`ops.alertsCenter.editor.${labels[kind]}`)
}
function metadataMessage(kind: MetadataKind) {
  const status = metadataStatus(kind)
  const suffix = status === 'loading' ? 'Loading' : status === 'error' ? 'LoadFailed' : 'Empty'
  return t(`ops.alertsCenter.editor.metadata${suffix}`, { field: metadataLabel(kind) })
}
const taskTypes = ref<string[]>([])
const taskEventTypes = ref<string[]>([])
const healthCheckTypes = ref<string[]>([])
const eventCategories = ref<string[]>([])
const eventTypesByCategory = ref<Record<string, string[]>>({})

const operatorOptions = ['>=', '>', '<', '<=', '==', '!='] as const

const pageTitle = computed(() =>
  editingId.value
    ? t('ops.alertsCenter.policies.editTitle')
    : t('ops.alertsCenter.common.createAlertPolicy'),
)
const pageSubtitle = computed(() =>
  editingId.value
    ? t('ops.alertsCenter.policies.editSubtitle')
    : t('ops.alertsCenter.policies.createSubtitle'),
)
const submitLabel = computed(() =>
  editingId.value ? t('common.save') : t('common.add'),
)

const durationOptions = computed(() => [
  { value: 60, label: t('ops.alertsCenter.editor.duration1m') },
  { value: 180, label: t('ops.alertsCenter.editor.duration3m') },
  { value: 300, label: t('ops.alertsCenter.editor.duration5m') },
  { value: 600, label: t('ops.alertsCenter.editor.duration10m') },
  { value: 900, label: t('ops.alertsCenter.editor.duration15m') },
  { value: 1800, label: t('ops.alertsCenter.editor.duration30m') },
])

/** Control Plane and Event rules have no selectable instance list—Scope stays All. */
const scopeSupportsSelection = computed(() =>
  form.type !== 'event' && form.resourceType !== 'system',
)
const scopeOptions = computed(() => {
  const options = [
    { value: 'all', label: t('ops.alertsCenter.editor.scopeAll') },
    { value: 'selected', label: t('ops.alertsCenter.editor.scopeSelected') },
  ]
  return scopeSupportsSelection.value ? options : options.filter((opt) => opt.value === 'all')
})
const showMonitoringResources = computed(() =>
  form.scope === 'selected' && scopeSupportsSelection.value,
)

function triggerField<K extends string>(key: K, fallback: unknown = '') {
  return computed({
    get: () => (form.triggerRule[key] ?? fallback) as never,
    set: (value) => {
      form.triggerRule[key] = value
    },
  })
}

function recoveryField<K extends string>(key: K, fallback: unknown = '') {
  return computed({
    get: () => (form.recoveryRule[key] ?? fallback) as never,
    set: (value) => {
      form.recoveryRule[key] = value
    },
  })
}

const metricKey = triggerField('metric_key', 'cpu_usage')
const metricOperator = triggerField('operator', '>=')
const metricThreshold = triggerField('threshold', 80)
const metricUnit = triggerField('unit', '%')
const metricDuration = triggerField('duration_seconds', 300)
const metricInterval = triggerField('evaluation_interval_seconds', 60)

const taskType = triggerField('task_type', 'backup')
const taskEventType = triggerField('event_type', 'task_failed')

const checkType = triggerField('check_type', 'heartbeat')
const healthTimeout = triggerField('timeout_seconds', 300)
const healthDuration = triggerField('duration_seconds', 300)
const healthHeartbeat = computed(() => String(form.triggerRule.check_type || 'heartbeat') === 'heartbeat')

const eventCategory = triggerField('event_category', 'configuration')
const eventTypes = triggerField('event_types', [] as string[])

const recoveryEnabled = recoveryField('enabled', true)
const recoveryOperator = computed({
  get: () => String(form.recoveryRule.operator || inverseOperator(
    form.triggerRule.operator,
  )),
  set: (value: string) => { form.recoveryRule.operator = value },
})
const recoveryThreshold = computed({
  get: () => Number(form.recoveryRule.threshold ?? form.triggerRule.threshold
    ?? 0),
  set: (value: number) => { form.recoveryRule.threshold = value },
})
const recoveryDuration = recoveryField('duration_seconds', 0)
const notifyOnFiring = recoveryField('notify_on_firing', true)
const notifyOnResolved = recoveryField('notify_on_resolved', true)
const repeatIntervalSeconds = triggerField('repeat_interval_seconds', 3600)
const mergeEnabled = triggerField('merge_enabled', false)
const mergeKey = triggerField('merge_key', '')
const mergeWindowSeconds = triggerField('merge_window_seconds', 300)

function customizeRecovery() {
  Object.assign(form.recoveryRule, { operator: '<', threshold: 70, duration_seconds: 180 })
}

function useManualRecovery() {
  form.recoveryRule = switchRecovery(form.recoveryRule, 'manual')
}

function handleMergeEnabledChange(enabled: boolean) {
  if (!enabled) return
  void nextTick(() => {
    const main = mainRef.value
    if (!main) return
    main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' })
  })
}

function formatSecondsLabel(seconds: number) {
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600
    return hours === 1
      ? t('ops.alertsCenter.editor.durationHour')
      : t('ops.alertsCenter.editor.durationHours', { n: hours })
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60
    return minutes === 1
      ? t('ops.alertsCenter.editor.durationMinute')
      : t('ops.alertsCenter.editor.durationMinutes', { n: minutes })
  }
  return seconds === 1
    ? t('ops.alertsCenter.editor.durationSecond')
    : t('ops.alertsCenter.editor.durationSecondsCount', { n: seconds })
}

function durationLabel(seconds?: unknown) {
  const value = Number(seconds)
  const match = durationOptions.value.find((item) => item.value === value)
  if (match) return match.label
  if (!Number.isFinite(value) || value <= 0) return '—'
  return formatSecondsLabel(value)
}

function boolLabel(value: unknown) {
  return value === false ? t('ops.alertsCenter.editor.no') : t('ops.alertsCenter.editor.yes')
}

function silenceLabel(value: unknown) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return t('ops.alertsCenter.editor.valueDisabled')
  }
  return durationLabel(seconds)
}

function scopeLabel(scope: string) {
  return scope === 'selected'
    ? t('ops.alertsCenter.editor.scopeSelected')
    : t('ops.alertsCenter.editor.scopeAll')
}

function severityLabel(severity: string) {
  const key = `ops.alertsCenter.common.${severity}` as const
  const translated = t(key)
  return translated !== key ? translated : severity
}

function severityTone(severity: string) {
  if (severity === 'critical') return 'danger'
  if (severity === 'info') return 'info'
  return 'warning'
}

const severityIcon = computed(() => {
  if (form.severity === 'critical') return TriangleAlert
  if (form.severity === 'info') return Info
  return CircleAlert
})

const previewChannels = computed(() => {
  if (!form.notificationChannelIds.length) {
    return PREVIEW_EMPTY_VALUE
  }
  const names = channels.value
    .filter((ch) => form.notificationChannelIds.includes(ch.id))
    .map((ch) => ch.name)
  return names.length ? names.join(', ') : PREVIEW_EMPTY_VALUE
})

const hasPreviewChannels = computed(() => previewChannels.value !== PREVIEW_EMPTY_VALUE)
const monitoringResourcesRequired = computed(() => showMonitoringResources.value)
const previewMonitoringResources = computed(() => {
  if (!monitoringResourcesRequired.value) return PREVIEW_EMPTY_VALUE
  const names = resourceOptions.value
    .filter((resource) => form.resourceIds.includes(resource.id))
    .map((resource) => resource.name)
  return names.length ? names.join(', ') : PREVIEW_EMPTY_VALUE
})

function currentTriggerSpecs() {
  return triggerSpecsForType(form.type)
}

const formRules = computed<FormRules>(() => ({
  name: [
    {
      required: true,
      validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
        if (String(value || '').trim()) {
          callback()
          return
        }
        callback(new Error(t('ops.alerts.rules.validateNameRequired')))
      },
      trigger: ['blur', 'change'],
    },
  ],
  resourceIds: [
    {
      required: monitoringResourcesRequired.value,
      message: t('ops.alertsCenter.editor.validateResourcesRequired'),
      validator: (_rule: unknown, value: string[] | undefined, callback: (error?: Error) => void) => {
        if (!monitoringResourcesRequired.value || (Array.isArray(value) && value.length > 0)) {
          callback()
          return
        }
        callback(new Error(t('ops.alertsCenter.editor.validateResourcesRequired')))
      },
      trigger: 'change',
    },
  ],
  ...Object.fromEntries(currentTriggerSpecs().map((spec) => [
    spec.path,
    [{
      required: true,
      validator: (_rule: unknown, value: unknown, callback: (error?: Error) => void) => {
        if (isValidTriggerValue(value, spec.kind)) {
          callback()
          return
        }
        callback(new Error(t('ops.alertsCenter.editor.validateTriggerRequired', {
          field: t(spec.labelKey),
        })))
      },
      trigger: 'change',
    }],
  ])),
}))

const previewTriggerText = computed(() => triggerSummary(
  form.type,
  form.triggerRule,
  t,
  {
    metricLabel,
    healthCheckLabel,
    taskTypeLabel,
    taskEventLabel,
    eventCategoryLabel,
    eventTypeLabel,
  },
))

const previewRecoveryText = computed(() => {
  const rule = form.recoveryRule
  if (currentRecoveryKind.value === 'manual') {
    return t(legacyTaskRecovery.value
      ? 'ops.alertsCenter.editor.recoveryLegacyTask'
      : 'ops.alertsCenter.editor.recoveryManual')
  }
  if (currentRecoveryKind.value === 'heartbeat') return t('ops.alertsCenter.editor.recoveryHeartbeat')
  if (legacyNumericRecovery.value) return t('ops.alertsCenter.editor.recoveryTriggerCleared')
  const duration = Number(rule.duration_seconds || 0) === 0
    ? t('ops.alertsCenter.editor.recoveryImmediate') : durationLabel(rule.duration_seconds)
  const unit = String(form.triggerRule.unit || '')
  const metric = metricLabel(String(form.triggerRule.metric_key || ''))
  return `${metric} ${recoveryOperator.value} ${recoveryThreshold.value}${unit} ${duration}`
})

const previewBasicsRows = computed(() => [
  { label: t('ops.alertsCenter.policies.alertType'), value: policyTypeLabel(form.type) },
  {
    label: t('ops.alertsCenter.common.severity'),
    value: severityLabel(form.severity),
    severity: form.severity,
    tone: severityTone(form.severity),
  },
  {
    label: t('ops.alertsCenter.editor.fieldEnabled'),
    value: form.enabled
      ? t('ops.alertsCenter.editor.valueEnabled')
      : t('ops.alertsCenter.editor.valueDisabled'),
    tone: form.enabled ? 'success' : 'muted',
  },
])

const previewTargetRows = computed(() => {
  const rows = [
    {
      label: t('ops.alertsCenter.policies.targetColumn'),
      value: resourceTypeLabel(form.resourceType),
    },
    {
      label: t('ops.alertsCenter.policies.scope'),
      value: scopeLabel(form.scope),
    },
  ]
  if (monitoringResourcesRequired.value) {
    rows.push({
      label: t('ops.alertsCenter.editor.previewMonitoringResources'),
      value: previewMonitoringResources.value,
    })
    rows.push({
      label: t('ops.alertsCenter.editor.previewResourceCount'),
      value: String(form.resourceIds.length),
    })
  }
  return rows
})

const previewTriggerRows = computed(() => [
  { label: t('ops.alertsCenter.editor.previewTrigger'), value: previewTriggerText.value },
])

const previewResolutionRows = computed(() => [
  {
    label: t('ops.alertsCenter.editor.previewAutoRecovery'),
    value: legacyTaskRecovery.value
      ? t('ops.alertsCenter.editor.recoveryLegacyLabel')
      : displayedAutoRecovery.value
        ? t('ops.alertsCenter.editor.valueEnabled')
        : t('ops.alertsCenter.editor.valueDisabled'),
    tone: displayedAutoRecovery.value && !legacyTaskRecovery.value ? 'success' : 'muted',
  },
  {
    label: t('ops.alertsCenter.editor.previewRecovery'),
    value: form.recoveryRule.enabled === false
      ? t('ops.alertsCenter.editor.recoveryManual')
      : previewRecoveryText.value,
  },
])

const previewNotificationRows = computed(() => [
  {
    label: t('ops.alertsCenter.policies.notificationChannels'),
    value: previewChannels.value,
    badge: true,
    empty: !hasPreviewChannels.value,
  },
  {
    label: t('ops.alertsCenter.editor.previewNotifyTimingLabel'),
    value: t('ops.alertsCenter.editor.previewNotifyTiming', {
      firing: boolLabel(form.recoveryRule.notify_on_firing),
      resolved: boolLabel(form.recoveryRule.notify_on_resolved),
    }),
  },
  {
    label: t('ops.alertsCenter.editor.fieldRepeatInterval'),
    value: silenceLabel(form.triggerRule.repeat_interval_seconds),
  },
  {
    label: t('ops.alertsCenter.editor.fieldMergeNotification'),
    value: form.triggerRule.merge_enabled
      ? t('ops.alertsCenter.editor.valueEnabled')
      : t('ops.alertsCenter.editor.valueDisabled'),
    tone: form.triggerRule.merge_enabled ? 'success' : 'muted',
  },
])

async function loadChannels() {
  try {
    if (isPlatformOps.value) {
      const options = await listPlatformNotificationChannelOptions()
      channels.value = options.map((c) => ({
        id: c.id,
        name: c.name,
        type: c.channel_type,
        enabled: c.is_active,
      })).filter((c) => c.enabled)
    } else {
      const res = await listChannels({ enabled: 'true', page_size: 200 })
      channels.value = res.results.filter((c) => c.enabled)
    }
  } catch {
    channels.value = []
  }
}

async function loadPolicy(id: string) {
  const row = isPlatformOps.value
    ? await fetchPlatformAlertPolicy(id) as Awaited<ReturnType<typeof getPolicy>>
    : await getPolicy(id)
  const loadedTriggerRule = row.triggerRule || row.trigger_rule || {}
  hydratingPolicy.value = true
  Object.assign(form, {
    name: row.name,
    description: row.description || '',
    type: row.type,
    severity: row.severity,
    enabled: row.enabled,
    resourceType: row.resourceType || row.resource_type || 'task',
    scope: row.scope,
    resourceIds: [...(row.resourceIds || row.resource_ids || [])],
    triggerRule: { ...defaultTriggerRule(row.type), ...loadedTriggerRule },
    recoveryRule: { ...(row.recoveryRule || row.recovery_rule || {}) },
    notificationChannelIds: [...(row.notificationChannelIds || row.notification_channel_ids || [])],
  })
  loadedRecovery.value = row.recoveryRule ?? row.recovery_rule ?? null
  loadedRecoverySnapshot.value = JSON.stringify(form.recoveryRule)
  await nextTick()
  hydratingPolicy.value = false
}

function policyPayload() {
  return {
    name: form.name,
    description: form.description,
    type: form.type,
    severity: form.severity,
    enabled: form.enabled,
    resource_type: form.resourceType,
    scope: form.scope,
    resource_ids: form.scope === 'selected' ? form.resourceIds : [],
    trigger_rule: form.triggerRule,
    recovery_rule: editingId.value && JSON.stringify(form.recoveryRule) === loadedRecoverySnapshot.value
      ? loadedRecovery.value : form.recoveryRule,
    notification_channel_ids: form.notificationChannelIds,
  }
}

function locateRequiredField(field: string) {
  void nextTick(() => {
    const root = pageRef.value || document
    const fieldEl = root.querySelector<HTMLElement>(`[data-alert-policy-field="${field}"]`)
    if (!fieldEl) return
    fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => {
      const focusEl = fieldEl.querySelector<HTMLElement>(
        'input:not([type="hidden"]), textarea, button, [tabindex]:not([tabindex="-1"])',
      )
      focusEl?.focus({ preventScroll: true })
    }, 240)
  })
}

function applyBackendTriggerErrors(error: unknown) {
  const specs = backendTriggerErrorSpecs(toApiError(error).fields, form.type)
  if (!specs.length) return

  void nextTick(() => {
    for (const spec of specs) {
      const field = formRef.value?.getField(spec.path)
      if (!field) continue
      field.validateState = 'error'
      field.validateMessage = t('ops.alertsCenter.editor.validateTriggerRequired', {
        field: t(spec.labelKey),
      })
    }
    locateRequiredField(specs[0].field)
  })
}

function markTriggerFieldError(spec: { path: string; field: string; labelKey: string }) {
  void nextTick(() => {
    const field = formRef.value?.getField(spec.path)
    if (!field) return
    field.validateState = 'error'
    field.validateMessage = t('ops.alertsCenter.editor.validateTriggerRequired', {
      field: t(spec.labelKey),
    })
  })
  locateRequiredField(spec.field)
}

async function validateRequiredFields() {
  if (metricConfigurationBlocked.value) {
    locateRequiredField('metric_key')
    return false
  }
  if (metadataConfigurationBlocked.value) {
    locateRequiredField(form.type === 'task' ? 'task_type' : 'event_category')
    return false
  }
  let formValid = true
  try {
    await formRef.value?.validate()
  } catch {
    formValid = false
  }
  if (!form.name.trim()) {
    locateRequiredField('name')
    return false
  }
  if (monitoringResourcesRequired.value && form.resourceIds.length === 0) {
    locateRequiredField('resources')
    return false
  }
  // Element Plus can treat [] as present for required rules; enforce trigger specs explicitly.
  const missingTrigger = currentTriggerSpecs().find((spec) => {
    const key = spec.path.slice('triggerRule.'.length)
    return !isValidTriggerValue(form.triggerRule[key], spec.kind)
  })
  if (missingTrigger) {
    markTriggerFieldError(missingTrigger)
    return false
  }
  return formValid
}

async function savePolicy() {
  if (!(await validateRequiredFields())) {
    return
  }
  saving.value = true
  try {
    const body = policyPayload()
    if (isPlatformOps.value) {
      if (editingId.value) {
        await updatePlatformAlertPolicy(editingId.value, body)
        ElMessage.success({ message: t('ops.alerts.rules.msgUpdated'), grouping: true })
      } else {
        await createPlatformAlertPolicy(body)
        ElMessage.success({ message: t('ops.alerts.rules.msgCreated'), grouping: true })
      }
    } else if (editingId.value) {
      await updatePolicy(editingId.value, body)
      ElMessage.success({ message: t('ops.alerts.rules.msgUpdated'), grouping: true })
    } else {
      await createPolicy(body)
      ElMessage.success({ message: t('ops.alerts.rules.msgCreated'), grouping: true })
    }
    router.push(listPath.value)
  } catch (e: unknown) {
    applyBackendTriggerErrors(e)
    ElMessage.error({
      message: apiErrorMessageI18n(e, t, t('ops.alerts.msgRequestError')),
      grouping: true,
    })
  } finally {
    saving.value = false
  }
}

function handleBack() {
  router.push(listPath.value)
}

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}

async function loadMetadataKind(kind: MetadataKind) {
  const state = metadataState[kind]
  const request = ++state.request
  state.status = 'loading'
  try {
    const raw = await fetchMetadata(
      kind,
      kind === 'health-check-types' && isPlatformOps.value ? { scope: 'platform' } : undefined,
    )
    if (request !== state.request) return
    if (kind === 'event-types') {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid event catalog')
      const payload = raw as Record<string, unknown>
      const tasks = payload.taskEventTypes ?? payload.task_event_types ?? []
      const categories = payload.categories ?? []
      const types = payload.types ?? {}
      if (!stringList(tasks) || !stringList(categories) || !types || typeof types !== 'object'
        || Array.isArray(types) || !Object.values(types).every(stringList)) throw new Error('Invalid event catalog')
      taskEventTypes.value = tasks
      eventCategories.value = categories
      eventTypesByCategory.value = types as Record<string, string[]>
      state.status = tasks.length || categories.length ? 'ready' : 'empty'
    } else {
      if (!stringList(raw)) throw new Error('Invalid rule catalog')
      if (kind === 'task-types') taskTypes.value = raw
      else if (kind === 'health-check-types') healthCheckTypes.value = raw
      state.status = raw.length ? 'ready' : 'empty'
    }
  } catch {
    if (request === state.request) state.status = 'error'
  }
}

async function loadMetadata() {
  await Promise.all(metadataKinds.map(loadMetadataKind))
}

async function loadMetricKeys(resetInvalid = false) {
  const requestId = ++metricRequestId
  metricCatalogLoaded.value = false
  metricKeysError.value = false
  if (form.type !== 'metric') {
    metricKeys.value = []
    metricKeysLoading.value = false
    return
  }
  metricKeysLoading.value = true
  metricKeys.value = []
  const applyKeys = (keys: string[]) => {
    metricKeys.value = orderMetricKeys(form.resourceType, keys)
    if (resetInvalid && !metricKeys.value.includes(String(metricKey.value))) {
      metricKey.value = metricKeys.value[0] || ''
    }
  }
  try {
    const raw = isPlatformOps.value
      ? await fetchPlatformAlertMetadataMetrics(form.resourceType)
      : await fetchMetadata('metrics', { resource_type: form.resourceType })
    if (requestId !== metricRequestId) return
    const keys = normalizeMetricCatalog(raw) ?? metricsForResourceType(form.resourceType)
    if (!keys.length && !metricsForResourceType(form.resourceType).length) {
      applyKeys([])
      return
    }
    applyKeys(keys.length ? keys : metricsForResourceType(form.resourceType))
  } catch {
    if (requestId !== metricRequestId) return
    // Static catalog fallback: Admin Console / missing org must still be editable.
    const fallback = metricsForResourceType(form.resourceType)
    if (fallback.length) {
      applyKeys(fallback)
      metricKeysError.value = false
    } else {
      metricKeys.value = []
      metricKeysError.value = true
    }
  } finally {
    if (requestId === metricRequestId) {
      metricKeysLoading.value = false
      metricCatalogLoaded.value = true
    }
  }
}

async function loadResources() {
  if (!showMonitoringResources.value || !form.resourceType) {
    resourceOptions.value = []
    return
  }
  resourcesLoading.value = true
  try {
    resourceOptions.value = isPlatformOps.value
      ? await fetchPlatformAlertMetadataResources(form.resourceType)
      : await fetchMetadataResources(form.resourceType)
  } finally {
    resourcesLoading.value = false
  }
}

function defaultTriggerRule(type: string): Record<string, unknown> {
  if (type === 'metric') {
    return {
      metric_key: 'cpu_usage',
      operator: '>=',
      threshold: 80,
      unit: '%',
      duration_seconds: 300,
      evaluation_interval_seconds: 60,
    }
  }
  if (type === 'task') {
    return {
      task_type: 'backup',
      event_type: 'task_failed',
    }
  }
  if (type === 'health' || type === 'availability') {
    return {
      check_type: 'heartbeat',
      timeout_seconds: 300,
      duration_seconds: 300,
    }
  }
  if (type === 'event') {
    return {
      event_category: 'configuration',
      event_types: [],
    }
  }
  return {}
}

function applyDefaultTriggerRule(type: string) {
  form.triggerRule = switchTrigger(form.triggerRule, defaultTriggerRule(type))
  form.recoveryRule = switchRecovery(form.recoveryRule, recoveryKind(type, form.triggerRule))
}

watch(
  () => form.type,
  (type, prev) => {
    if (type === prev || hydratingPolicy.value) return
    applyDefaultTriggerRule(type)
    if (!isAllowedAlertResourceType(type, form.resourceType, isPlatformOps.value)) {
      form.resourceType = defaultResourceTypeFor(type, isPlatformOps.value)
    }
    void loadMetricKeys()
    if (!monitoringResourcesRequired.value) {
      formRef.value?.clearValidate('resourceIds')
    }
    void nextTick(() => {
      const paths = [
        ...(triggerValidationSpecs[prev] || []).map((spec) => spec.path),
        ...currentTriggerSpecs().map((spec) => spec.path),
      ]
      formRef.value?.clearValidate(paths)
    })
  },
)

watch(
  () => [form.type, currentRecoveryKind.value] as const,
  ([type, kind], [previousType, previousKind]) => {
    if (!hydratingPolicy.value && type === previousType && kind !== previousKind) {
      form.recoveryRule = switchRecovery(form.recoveryRule, kind)
    }
  },
)

watch(
  () => [form.resourceType, form.scope, form.type] as const,
  ([resourceType], previous) => {
    const resourceTypeChanged = resourceType !== previous?.[0]
    if (!hydratingPolicy.value) {
      form.resourceIds = []
      if (!scopeSupportsSelection.value && form.scope !== 'all') {
        form.scope = 'all'
      }
    }
    void loadMetricKeys(resourceTypeChanged && !hydratingPolicy.value)
    void loadResources()
    if (!monitoringResourcesRequired.value) {
      formRef.value?.clearValidate('resourceIds')
    }
  },
)

watch(
  () => String(metricKey.value || ''),
  (key) => {
    if (hydratingPolicy.value || form.type !== 'metric') return
    syncMetricUnit(key)
  },
)

watch(hydratingPolicy, (loading) => {
  if (!loading && form.type === 'metric') syncMetricUnit()
})

watch(
  () => eventCategory.value,
  (category) => {
    if (hydratingPolicy.value) return
    const options = eventTypesByCategory.value[category] || []
    if (!Array.isArray(eventTypes.value) || !eventTypes.value.length) return
    eventTypes.value = eventTypes.value.filter((item) => options.includes(item))
  },
)

onMounted(async () => {
  await loadChannels()
  if (editingId.value) {
    try {
      await loadPolicy(editingId.value)
    } catch (e: unknown) {
      ElMessage.error({
        message: apiErrorMessageI18n(e, t, t('ops.alerts.msgRequestError')),
        grouping: true,
      })
      handleBack()
      return
    }
  }
  await Promise.all([loadMetadata(), loadMetricKeys(), loadResources()])
})

onBeforeUnmount(() => {
  metricRequestId++
  for (const kind of metadataKinds) metadataState[kind].request++
})
</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen alert-policy-editor-fullscreen"
  >
    <div class="fullscreen-form-page add-s3-page alert-policy-editor-page">
      <div class="fullscreen-form-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          @click="handleBack"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ pageTitle }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ pageSubtitle }}
          </p>
        </div>
      </div>

      <div class="fullscreen-form-layout">
        <div
          ref="mainRef"
          class="fullscreen-form-main"
        >
          <el-form
            ref="formRef"
            :model="form"
            :rules="formRules"
            :validate-on-rule-change="false"
            class="fullscreen-form-el-form alert-policy-editor-form"
            label-position="top"
            require-asterisk-position="right"
          >
            <div class="fullscreen-form-step-stack">
              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.alertsCenter.editor.sectionBasic') }}
                </h3>
                <div class="fullscreen-form-grid">
                  <el-form-item
                    :label="t('ops.alertsCenter.common.name')"
                    prop="name"
                    class="fullscreen-form-item--in-card"
                    data-alert-policy-field="name"
                  >
                    <el-input
                      v-model="form.name"
                      :placeholder="t('ops.alertsCenter.policies.alertNamePlaceholder')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldNameHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.policies.alertType')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.type"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in policyTypeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ policyTypeDescription(form.type) }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.common.severity')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.severity"
                      class="w-full"
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
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldSeverityHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldEnabled')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch v-model="form.enabled" />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldEnabledHint') }}
                    </p>
                  </el-form-item>
                </div>
                <el-form-item
                  :label="t('ops.alertsCenter.policies.description')"
                  class="fullscreen-form-item--in-card"
                >
                  <el-input
                    v-model="form.description"
                    type="textarea"
                    :rows="3"
                    :placeholder="t('ops.alertsCenter.policies.descriptionPlaceholder')"
                  />
                  <p class="fullscreen-form-field__hint">
                    {{ t('ops.alertsCenter.editor.fieldDescriptionHint') }}
                  </p>
                </el-form-item>
              </section>

              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.alertsCenter.editor.sectionTarget') }}
                </h3>
                <div class="fullscreen-form-grid">
                  <el-form-item
                    :label="t('ops.alertsCenter.policies.targetColumn')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.resourceType"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in selectableResourceTypeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{
                        resourceTypeDescription(form.resourceType)
                          || t('ops.alertsCenter.editor.fieldResourceTypeHint')
                      }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.policies.scope')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.scope"
                      class="w-full"
                      :disabled="!scopeSupportsSelection"
                    >
                      <el-option
                        v-for="opt in scopeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{
                        scopeSupportsSelection
                          ? t('ops.alertsCenter.editor.fieldScopeHint')
                          : t('ops.alertsCenter.editor.fieldScopeFixedHint')
                      }}
                    </p>
                  </el-form-item>
                </div>
                <el-form-item
                  v-if="showMonitoringResources"
                  :label="t('ops.alertsCenter.editor.fieldMonitoringResources')"
                  prop="resourceIds"
                  class="fullscreen-form-item--in-card mt-2"
                  data-alert-policy-field="resources"
                >
                  <div class="fullscreen-form-control-row">
                    <el-select
                      v-model="form.resourceIds"
                      multiple
                      filterable
                      collapse-tags
                      collapse-tags-tooltip
                      fit-input-width
                      :max-collapse-tags="2"
                      popper-class="alert-policy-editor-resource-select-popper"
                      class="fullscreen-form-control-main alert-policy-editor-resource-select"
                      :loading="resourcesLoading"
                      :placeholder="t('ops.alertsCenter.editor.phMonitoringResources')"
                    >
                      <el-option
                        v-for="resource in resourceOptions"
                        :key="resource.id"
                        :value="resource.id"
                        :label="`${resource.name}${resource.status ? ` · ${resource.status}` : ''}`"
                      />
                    </el-select>
                    <el-button
                      class="fullscreen-form-icon-btn hfl-refresh-button"
                      native-type="button"
                      :title="t('ops.alertsCenter.editor.refreshResources')"
                      :aria-label="t('ops.alertsCenter.editor.refreshResources')"
                      :disabled="resourcesLoading"
                      @click="loadResources"
                    >
                      <RefreshCw
                        :size="16"
                        :class="{ 'is-spinning': resourcesLoading }"
                      />
                    </el-button>
                  </div>
                  <p class="fullscreen-form-field__hint">
                    {{ t('ops.alertsCenter.editor.resourceMultiSelectHint') }}
                  </p>
                </el-form-item>
              </section>

              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.alertsCenter.editor.sectionTrigger') }}
                </h3>

                <div
                  v-if="form.type === 'metric'"
                  class="fullscreen-form-grid"
                >
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldMetric')"
                    prop="triggerRule.metric_key"
                    data-alert-policy-field="metric_key"
                  >
                    <el-select
                      v-model="metricKey"
                      class="w-full"
                      filterable
                      :loading="metricKeysLoading"
                      :disabled="!metricCatalogLoaded || metricKeysLoading || metricKeysError || !metricKeys.length"
                      :placeholder="t('ops.alertsCenter.editor.phMetric')"
                      :no-data-text="metricCatalogHint"
                    >
                      <el-option
                        v-for="opt in metricOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldMetricHint') }}
                    </p>
                    <p
                      v-if="metricCatalogHint"
                      class="fullscreen-form-field__hint"
                      role="status"
                    >
                      {{ metricCatalogHint }}
                    </p>
                    <el-button
                      v-if="metricKeysError"
                      @click="loadMetricKeys(true)"
                    >
                      {{ t('ops.alertsCenter.editor.metricRetry') }}
                    </el-button>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldOperator')"
                    prop="triggerRule.operator"
                    data-alert-policy-field="operator"
                  >
                    <el-select
                      v-model="metricOperator"
                      class="w-full"
                      :placeholder="t('ops.alertsCenter.editor.phOperator')"
                    >
                      <el-option
                        v-for="op in operatorOptions"
                        :key="op"
                        :value="op"
                        :label="op"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldOperatorHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldThreshold')"
                    prop="triggerRule.threshold"
                    data-alert-policy-field="threshold"
                  >
                    <div class="flex w-full items-center gap-2">
                      <el-input-number
                        v-model="metricThreshold"
                        class="flex-1"
                        :min="0"
                      />
                      <el-select
                        v-if="metricUnitSelectable"
                        v-model="metricUnit"
                        class="alert-policy-editor-threshold-unit"
                      >
                        <el-option
                          v-for="opt in metricUnitChoices"
                          :key="opt.value || 'none'"
                          :value="opt.value"
                          :label="opt.label"
                        />
                      </el-select>
                      <span
                        v-else-if="showMetricUnitField"
                        class="fullscreen-form-field__unit"
                      >
                        {{ metricUnitLabel }}
                      </span>
                    </div>
                    <p class="fullscreen-form-field__hint">
                      {{
                        metricUnitSelectable
                          ? t('ops.alertsCenter.editor.fieldThresholdUnitHint')
                          : t('ops.alertsCenter.editor.fieldThresholdHint')
                      }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldDuration')"
                    prop="triggerRule.duration_seconds"
                    data-alert-policy-field="duration_seconds"
                  >
                    <el-select
                      v-model="metricDuration"
                      class="w-full"
                      :placeholder="t('ops.alertsCenter.editor.phDuration')"
                    >
                      <el-option
                        v-for="opt in durationOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldDurationHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldEvaluationInterval')"
                    prop="triggerRule.evaluation_interval_seconds"
                    data-alert-policy-field="evaluation_interval_seconds"
                  >
                    <el-select
                      v-model="metricInterval"
                      class="w-full"
                      :placeholder="t('ops.alertsCenter.editor.phCheckInterval')"
                    >
                      <el-option
                        v-for="opt in durationOptions"
                        :key="`interval-${opt.value}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldEvaluationIntervalHint') }}
                    </p>
                  </el-form-item>
                </div>

                <div
                  v-else-if="form.type === 'task'"
                  class="fullscreen-form-grid"
                >
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.taskType')"
                    prop="triggerRule.task_type"
                    data-alert-policy-field="task_type"
                  >
                    <el-select
                      v-model="taskType"
                      class="w-full"
                      :loading="metadataState['task-types'].status === 'loading'"
                      :disabled="metadataState['task-types'].status !== 'ready'"
                      :placeholder="t('ops.alertsCenter.editor.phTaskType')"
                    >
                      <el-option
                        v-for="opt in taskTypeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.taskTypeHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.eventType')"
                    prop="triggerRule.event_type"
                    data-alert-policy-field="event_type"
                  >
                    <el-select
                      v-model="taskEventType"
                      class="w-full"
                      :loading="metadataState['event-types'].status === 'loading'"
                      :disabled="metadataState['event-types'].status !== 'ready'"
                      :placeholder="t('ops.alertsCenter.editor.phEventType')"
                    >
                      <el-option
                        v-for="opt in taskEventOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.eventTypeHint') }}
                    </p>
                  </el-form-item>
                </div>

                <div
                  v-else-if="form.type === 'health' || form.type === 'availability'"
                  class="fullscreen-form-grid"
                >
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.checkType')"
                    prop="triggerRule.check_type"
                    data-alert-policy-field="check_type"
                  >
                    <el-select
                      v-model="checkType"
                      class="w-full"
                      :loading="metadataState['health-check-types'].status === 'loading'"
                      :disabled="metadataState['health-check-types'].status !== 'ready'"
                      :placeholder="t('ops.alertsCenter.editor.phCheckType')"
                    >
                      <el-option
                        v-for="opt in healthCheckOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.checkTypeHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldDuration')"
                    prop="triggerRule.duration_seconds"
                    data-alert-policy-field="duration_seconds"
                  >
                    <el-select
                      v-model="healthDuration"
                      class="w-full"
                      :placeholder="t('ops.alertsCenter.editor.phDuration')"
                    >
                      <el-option
                        v-for="opt in durationOptions"
                        :key="`health-${opt.value}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldDurationHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    v-if="healthHeartbeat"
                    :label="t('ops.alertsCenter.editor.fieldAvailabilityTimeout')"
                    prop="triggerRule.timeout_seconds"
                    data-alert-policy-field="timeout_seconds"
                  >
                    <div class="flex w-full items-center gap-2">
                      <el-input-number
                        v-model="healthTimeout"
                        class="flex-1"
                        :min="0"
                      />
                      <span class="fullscreen-form-field__unit">
                        {{ t('ops.alertsCenter.editor.unitSeconds') }}
                      </span>
                    </div>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldAvailabilityTimeoutHint') }}
                    </p>
                  </el-form-item>
                </div>

                <div
                  v-else-if="form.type === 'event'"
                  class="fullscreen-form-grid"
                >
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.eventCategory')"
                    prop="triggerRule.event_category"
                    data-alert-policy-field="event_category"
                  >
                    <el-select
                      v-model="eventCategory"
                      class="w-full"
                      :loading="metadataState['event-types'].status === 'loading'"
                      :disabled="metadataState['event-types'].status !== 'ready'"
                      :placeholder="t('ops.alertsCenter.editor.phEventCategory')"
                    >
                      <el-option
                        v-for="opt in eventCategoryOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.eventCategoryHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.eventTypes')"
                    prop="triggerRule.event_types"
                    data-alert-policy-field="event_types"
                  >
                    <el-select
                      v-model="eventTypes"
                      :loading="metadataState['event-types'].status === 'loading'"
                      :disabled="metadataState['event-types'].status !== 'ready'"
                      multiple
                      filterable
                      collapse-tags
                      collapse-tags-tooltip
                      :max-collapse-tags="1"
                      :tag-tooltip="{ popperClass: 'alert-policy-editor-event-types-tooltip', placement: 'bottom' }"
                      :placeholder="t('ops.alertsCenter.editor.phEventTypes')"
                      class="w-full alert-policy-editor-event-types"
                    >
                      <el-option
                        v-for="opt in platformEventOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.eventTypesHint') }}
                    </p>
                  </el-form-item>
                </div>
                <template
                  v-for="kind in requiredMetadataKinds"
                  :key="kind"
                >
                  <div
                    v-if="metadataStatus(kind) !== 'ready'"
                    role="status"
                  >
                    <p class="fullscreen-form-field__hint">
                      {{ metadataMessage(kind) }}
                    </p>
                    <el-button
                      v-if="metadataStatus(kind) !== 'loading'"
                      @click="loadMetadataKind(kind)"
                    >
                      {{ t('ops.alertsCenter.editor.metadataRetry') }}
                    </el-button>
                  </div>
                </template>
                <p
                  v-if="requiredMetadataKinds.length && requiredMetadataKinds.every(kind => metadataStatus(kind) === 'ready')
                    && !metadataSelectionValid"
                  class="fullscreen-form-field__hint"
                  role="status"
                >
                  {{ t('ops.alertsCenter.editor.metadataSelectAvailable') }}
                </p>
              </section>

              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.alertsCenter.editor.sectionRecovery') }}
                </h3>
                <template v-if="currentRecoveryKind === 'manual'">
                  <p class="fullscreen-form-field__label">
                    {{ t(legacyTaskRecovery
                      ? 'ops.alertsCenter.editor.recoveryLegacyLabel'
                      : 'ops.alertsCenter.editor.recoveryManual') }}
                  </p>
                  <p class="fullscreen-form-field__hint">
                    {{ legacyTaskRecovery ? previewRecoveryText
                      : t(form.type === 'task'
                        ? 'ops.alertsCenter.editor.recoveryManualTaskHint'
                        : 'ops.alertsCenter.editor.recoveryManualHint') }}
                  </p>
                  <el-button
                    v-if="legacyTaskRecovery"
                    @click="useManualRecovery"
                  >
                    {{ t('ops.alertsCenter.editor.recoveryUseManual') }}
                  </el-button>
                </template>
                <el-form-item
                  v-else
                  :label="t('ops.alertsCenter.editor.fieldRecoveryEnabled')"
                  class="fullscreen-form-status-item"
                >
                  <el-switch v-model="recoveryEnabled" />
                  <p class="fullscreen-form-field__hint">
                    {{ currentRecoveryKind === 'heartbeat'
                      ? t('ops.alertsCenter.editor.recoveryHeartbeatHint')
                      : t('ops.alertsCenter.editor.fieldRecoveryEnabledHint') }}
                  </p>
                </el-form-item>
                <template v-if="recoveryEnabled && legacyNumericRecovery">
                  <p class="fullscreen-form-field__hint">
                    {{ t('ops.alertsCenter.editor.recoveryTriggerCleared') }}
                  </p>
                  <el-button @click="customizeRecovery">
                    {{ t('ops.alertsCenter.editor.recoveryCustomize') }}
                  </el-button>
                </template>
                <div
                  v-if="recoveryEnabled && currentRecoveryKind === 'numeric' && !legacyNumericRecovery"
                  class="fullscreen-form-grid mt-3"
                >
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldOperator')">
                    <el-select
                      v-model="recoveryOperator"
                      class="w-full"
                      :placeholder="t('ops.alertsCenter.editor.phOperator')"
                    >
                      <el-option
                        v-for="op in operatorOptions"
                        :key="`recovery-${op}`"
                        :value="op"
                        :label="op"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldOperatorHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldThreshold')">
                    <div class="flex w-full items-center gap-2">
                      <el-input-number
                        v-model="recoveryThreshold"
                        class="flex-1"
                        :min="0"
                      />
                      <el-select
                        v-if="metricUnitSelectable"
                        v-model="metricUnit"
                        class="alert-policy-editor-threshold-unit"
                      >
                        <el-option
                          v-for="opt in metricUnitChoices"
                          :key="`recovery-unit-${opt.value || 'none'}`"
                          :value="opt.value"
                          :label="opt.label"
                        />
                      </el-select>
                      <span
                        v-else-if="showMetricUnitField"
                        class="fullscreen-form-field__unit"
                      >
                        {{ metricUnitLabel }}
                      </span>
                    </div>
                    <p class="fullscreen-form-field__hint">
                      {{
                        metricUnitSelectable
                          ? t('ops.alertsCenter.editor.fieldRecoveryThresholdUnitHint')
                          : t('ops.alertsCenter.editor.fieldRecoveryThresholdHint')
                      }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldDuration')">
                    <el-select
                      v-model="recoveryDuration"
                      class="w-full"
                      :placeholder="t('ops.alertsCenter.editor.phDuration')"
                    >
                      <el-option
                        :value="0"
                        :label="t('ops.alertsCenter.editor.recoveryImmediate')"
                      />
                      <el-option
                        v-for="opt in durationOptions"
                        :key="`recovery-${opt.value}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldRecoveryDurationHint') }}
                    </p>
                  </el-form-item>
                </div>
              </section>

              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.alertsCenter.editor.sectionNotification') }}
                </h3>
                <div class="fullscreen-form-grid">
                  <el-form-item :label="t('ops.alertsCenter.policies.notificationChannels')">
                    <el-select
                      v-model="form.notificationChannelIds"
                      multiple
                      class="w-full"
                      filterable
                      :placeholder="t('ops.alertsCenter.policies.notificationChannelsPlaceholder')"
                    >
                      <el-option
                        v-for="ch in channels"
                        :key="ch.id"
                        :value="ch.id"
                        :label="ch.name"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldNotificationChannelsHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldNotifyOnFiring')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch v-model="notifyOnFiring" />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldNotifyOnFiringHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldNotifyOnResolved')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch v-model="notifyOnResolved" />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldNotifyOnResolvedHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldRepeatInterval')">
                    <div class="flex w-full items-center gap-2">
                      <el-input-number
                        v-model="repeatIntervalSeconds"
                        class="flex-1"
                        :min="0"
                        :step="60"
                      />
                      <span class="fullscreen-form-field__unit">
                        {{ t('ops.alertsCenter.editor.unitSeconds') }}
                      </span>
                    </div>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldRepeatIntervalHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldMergeNotification')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch
                      v-model="mergeEnabled"
                      @change="handleMergeEnabledChange"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldMergeNotificationHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    v-if="mergeEnabled"
                    :label="t('ops.alertsCenter.editor.fieldMergeKey')"
                  >
                    <el-input
                      v-model="mergeKey"
                      :placeholder="t('ops.alertsCenter.editor.phMergeKey')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldMergeKeyHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    v-if="mergeEnabled"
                    :label="t('ops.alertsCenter.editor.fieldMergeWindow')"
                  >
                    <div class="flex w-full items-center gap-2">
                      <el-input-number
                        v-model="mergeWindowSeconds"
                        class="flex-1"
                        :min="60"
                        :step="60"
                      />
                      <span class="fullscreen-form-field__unit">
                        {{ t('ops.alertsCenter.editor.unitSeconds') }}
                      </span>
                    </div>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldMergeWindowHint') }}
                    </p>
                  </el-form-item>
                </div>
              </section>
            </div>
          </el-form>

          <div class="fullscreen-form-footer fullscreen-form-action-footer alert-policy-editor-footer">
            <ElButton @click="handleBack">
              {{ t('common.cancel') }}
            </ElButton>
            <ElButton
              type="primary"
              :loading="saving"
              :disabled="saving || metricConfigurationBlocked || metadataConfigurationBlocked"
              @click="savePolicy"
            >
              {{ submitLabel }}
            </ElButton>
          </div>
        </div>

        <aside class="fullscreen-form-sidebar add-form-preview-sidebar alert-policy-editor-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon alert-policy-editor-preview__icon">
                <BellRing
                  class="add-form-preview-header__cloud"
                  :size="28"
                />
              </div>
              <div class="add-form-preview-header__info">
                <h4 class="add-form-preview-header__name">
                  {{ form.name.trim() || t('ops.alertsCenter.policies.unnamedPolicy') }}
                </h4>
                <p class="add-form-preview-header__type">
                  {{ policyTypeLabel(form.type) }}
                </p>
              </div>
            </div>

            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.alertsCenter.editor.sectionBasic') }}
                </h5>
                <div
                  v-for="row in previewBasicsRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="[
                      { 'add-form-preview-row__value--empty': row.value === '—' },
                      row.tone ? `add-form-preview-row__value--${row.tone}` : '',
                    ]"
                  >
                    <component
                      :is="severityIcon"
                      v-if="row.severity"
                      class="alert-policy-editor-preview__severity-icon"
                      :size="14"
                    />
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.alertsCenter.editor.sectionTarget') }}
                </h5>
                <div
                  v-for="row in previewTargetRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': row.value === '—' }"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.alertsCenter.editor.sectionTrigger') }}
                </h5>
                <div
                  v-for="row in previewTriggerRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': row.value === '—' }"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.alertsCenter.editor.sectionRecovery') }}
                </h5>
                <div
                  v-for="row in previewResolutionRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="row.tone === 'success'
                      ? 'add-form-preview-row__value--success'
                      : row.tone === 'muted'
                        ? 'add-form-preview-row__value--muted'
                        : ''"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.alertsCenter.editor.sectionNotification') }}
                </h5>
                <div
                  v-for="row in previewNotificationRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="[
                      row.badge ? 'add-form-preview-row__value--badge' : '',
                      row.empty || row.value === '—' ? 'add-form-preview-row__value--empty' : '',
                      row.tone ? `add-form-preview-row__value--${row.tone}` : '',
                    ]"
                  >
                    <span
                      v-if="row.badge && !row.empty"
                      class="add-form-preview-row__dot add-form-preview-row__dot--primary"
                    />
                    {{ row.value }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style src="../../styles/alert-policy-editor.css"></style>
