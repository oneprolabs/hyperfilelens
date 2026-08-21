<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { ArrowLeft, BellRing, CircleAlert, Info, RefreshCw, TriangleAlert } from 'lucide-vue-next'
import { useAlertPolicyLabels } from '../../composables/useAlertPolicyLabels'
import {
  createPolicy,
  fetchMetadata,
  fetchMetadataResources,
  getPolicy,
  updatePolicy,
} from '../../lib/alertApi'
import { listChannels } from '../../lib/notificationApi'
import { apiErrorMessageI18n } from '../../lib/api'
import { toApiError } from '../../lib/errors'
import {
  backendTriggerErrorSpecs,
  isValidTriggerValue,
  triggerSpecsForType,
  triggerValidationSpecs,
} from './alertPolicyValidation'

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
    type: 'metric',
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
const { policyTypeLabel, policyTypeOptions, resourceTypeOptions, resourceTypeLabel } = useAlertPolicyLabels()
const PREVIEW_EMPTY_VALUE = '—'

const saving = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const mainRef = ref<HTMLElement | null>(null)
const formRef = ref<FormInstance | null>(null)
const form = reactive(defaultForm())
const channels = ref<Array<{ id: number; name: string }>>([])
const editingId = computed(() => {
  const id = route.params.id
  return typeof id === 'string' && id ? id : null
})

const resourcesLoading = ref(false)
const resourceOptions = ref<Array<{ id: string; name: string; status?: string }>>([])
const metricKeys = ref<string[]>([])
const taskTypes = ref<string[]>([])
const taskEventTypes = ref<string[]>([])
const availabilityCheckTypes = ref<string[]>([])
const systemCheckTypes = ref<string[]>([])
const eventCategories = ref<string[]>([])
const eventTypesByCategory = ref<Record<string, string[]>>({})

const operatorOptions = ['>=', '>', '<', '<=', '==', '!='] as const

const pageTitle = computed(() =>
  editingId.value
    ? t('ops.alertsCenter.policies.editTitle')
    : t('ops.alertsCenter.common.createAlertPolicy'),
)

const durationOptions = computed(() => [
  { value: 60, label: t('ops.alertsCenter.editor.duration1m') },
  { value: 180, label: t('ops.alertsCenter.editor.duration3m') },
  { value: 300, label: t('ops.alertsCenter.editor.duration5m') },
  { value: 600, label: t('ops.alertsCenter.editor.duration10m') },
  { value: 900, label: t('ops.alertsCenter.editor.duration15m') },
  { value: 1800, label: t('ops.alertsCenter.editor.duration30m') },
])

const scopeOptions = computed(() => [
  { value: 'all', label: t('ops.alertsCenter.editor.scopeAll') },
  { value: 'selected', label: t('ops.alertsCenter.editor.scopeSelected') },
])

const yesNoOptions = computed(() => [
  { value: true, label: t('ops.alertsCenter.editor.yes') },
  { value: false, label: t('ops.alertsCenter.editor.no') },
])

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
const availabilityTimeout = triggerField('timeout_seconds', 300)
const availabilityDuration = triggerField('duration_seconds', 300)

const systemCheckType = triggerField('check_type', 'service_health')
const systemDuration = triggerField('duration_seconds', 300)

const eventCategory = triggerField('event_category', 'configuration')
const eventTypes = triggerField('event_types', [] as string[])

const recoveryEnabled = recoveryField('enabled', true)
const recoveryCondition = recoveryField('condition', 'below_threshold')
const recoveryOperator = recoveryField('operator', '<')
const recoveryThreshold = recoveryField('threshold', 70)
const recoveryDuration = recoveryField('duration_seconds', 180)
const notifyOnFiring = recoveryField('notify_on_firing', true)
const notifyOnResolved = recoveryField('notify_on_resolved', true)
const repeatIntervalSeconds = triggerField('repeat_interval_seconds', 3600)
const mergeEnabled = triggerField('merge_enabled', false)
const mergeKey = triggerField('merge_key', '')
const mergeWindowSeconds = triggerField('merge_window_seconds', 300)

function handleMergeEnabledChange(enabled: boolean) {
  if (!enabled) return
  void nextTick(() => {
    const main = mainRef.value
    if (!main) return
    main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' })
  })
}

function durationLabel(seconds?: unknown) {
  const value = Number(seconds)
  const match = durationOptions.value.find((item) => item.value === value)
  if (match) return match.label
  if (!Number.isFinite(value) || value <= 0) return '—'
  return t('ops.alertsCenter.editor.previewForDuration', { duration: `${value}s` })
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
const monitoringResourcesRequired = computed(() =>
  form.scope === 'selected' && form.type !== 'event' && form.resourceType !== 'system',
)

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

const previewTriggerText = computed(() => {
  const rule = form.triggerRule
  if (form.type === 'metric') {
    return `${rule.metric_key || '—'} ${rule.operator || '>='} ${rule.threshold ?? '—'}${rule.unit || ''} ${durationLabel(rule.duration_seconds)}`
  }
  if (form.type === 'task') {
    return `${rule.task_type || '—'} / ${rule.event_type || '—'}`
  }
  if (form.type === 'availability') {
    return `${rule.check_type || 'heartbeat'} · ${durationLabel(rule.duration_seconds)}`
  }
  if (form.type === 'system') {
    return `${rule.check_type || 'service_health'} · ${durationLabel(rule.duration_seconds)}`
  }
  if (form.type === 'event') {
    const types = Array.isArray(rule.event_types) ? rule.event_types.join(', ') : '—'
    return `${rule.event_category || '—'} · ${types}`
  }
  return '—'
})

const previewRecoveryText = computed(() => {
  const rule = form.recoveryRule
  return `${rule.condition || 'below_threshold'} ${rule.operator || '<'} ${rule.threshold ?? '—'} ${durationLabel(rule.duration_seconds)}`
})

const previewBasicRows = computed(() => {
  const rows = [
    { label: t('ops.alertsCenter.common.type'), value: policyTypeLabel(form.type) },
    { label: t('ops.alertsCenter.common.severity'), value: severityLabel(form.severity), severity: form.severity, tone: severityTone(form.severity) },
    {
      label: t('ops.alertsCenter.editor.previewTarget'),
      value: `${resourceTypeLabel(form.resourceType)} / ${scopeLabel(form.scope)}`,
    },
  ]
  if (monitoringResourcesRequired.value) {
    rows.push({
      label: t('ops.alertsCenter.editor.previewResourceCount'),
      value: String(form.resourceIds.length),
    })
  }
  return rows
})

const previewRuleRows = computed(() => [
  { label: t('ops.alertsCenter.editor.previewTrigger'), value: previewTriggerText.value },
  {
    label: t('ops.alertsCenter.editor.previewAutoRecovery'),
    value: form.recoveryRule.enabled === false
      ? t('ops.alertsCenter.editor.valueDisabled')
      : t('ops.alertsCenter.editor.valueEnabled'),
    tone: form.recoveryRule.enabled === false ? 'muted' : 'success',
  },
  ...(form.recoveryRule.enabled === false ? [] : [
    { label: t('ops.alertsCenter.editor.previewRecovery'), value: previewRecoveryText.value },
  ]),
])

async function loadChannels() {
  try {
    const res = await listChannels({ enabled: 'true', page_size: 200 })
    channels.value = res.results.filter((c) => c.enabled)
  } catch {
    channels.value = []
  }
}

async function loadPolicy(id: string) {
  const row = await getPolicy(id)
  const loadedTriggerRule = row.triggerRule || row.trigger_rule || {}
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
    recoveryRule: { ...(row.recoveryRule || row.recovery_rule || { enabled: true }) },
    notificationChannelIds: [...(row.notificationChannelIds || row.notification_channel_ids || [])],
  })
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
    recovery_rule: form.recoveryRule,
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

async function validateRequiredFields() {
  try {
    await formRef.value?.validate()
    return true
  } catch {
    if (!form.name.trim()) {
      locateRequiredField('name')
    } else if (monitoringResourcesRequired.value && form.resourceIds.length === 0) {
      locateRequiredField('resources')
    } else {
      const missingTrigger = currentTriggerSpecs().find((spec) => {
        const key = spec.path.slice('triggerRule.'.length)
        return !isValidTriggerValue(form.triggerRule[key], spec.kind)
      })
      if (missingTrigger) locateRequiredField(missingTrigger.field)
    }
    return false
  }
}

async function savePolicy() {
  if (!(await validateRequiredFields())) {
    return
  }
  saving.value = true
  try {
    const body = policyPayload()
    if (editingId.value) {
      await updatePolicy(editingId.value, body)
      ElMessage.success({ message: t('ops.alerts.rules.msgUpdated'), grouping: true })
    } else {
      await createPolicy(body)
      ElMessage.success({ message: t('ops.alerts.rules.msgCreated'), grouping: true })
    }
    router.push('/ops/alerts/rules')
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
  router.push('/ops/alerts/rules')
}

async function loadMetadata() {
  const [tasks, events, availability, system] = await Promise.all([
    fetchMetadata('task-types'),
    fetchMetadata('event-types'),
    fetchMetadata('availability-check-types'),
    fetchMetadata('system-check-types'),
  ])
  taskTypes.value = Array.isArray(tasks) ? (tasks as string[]) : []
  const eventPayload = events as {
    taskEventTypes?: string[]
    task_event_types?: string[]
    categories?: string[]
    types?: Record<string, string[]>
  }
  taskEventTypes.value = eventPayload.taskEventTypes || eventPayload.task_event_types || []
  availabilityCheckTypes.value = Array.isArray(availability) ? (availability as string[]) : []
  systemCheckTypes.value = Array.isArray(system) ? (system as string[]) : []
  eventCategories.value = eventPayload.categories || []
  eventTypesByCategory.value = eventPayload.types || {}
}

async function loadMetricKeys() {
  if (form.type !== 'metric' && form.type !== 'system') {
    metricKeys.value = []
    return
  }
  try {
    const raw = await fetchMetadata('metrics', { resource_type: form.resourceType })
    metricKeys.value = Array.isArray(raw) ? (raw as string[]) : []
  } catch {
    metricKeys.value = []
  }
}

async function loadResources() {
  if (form.scope !== 'selected' || form.type === 'event' || !form.resourceType) {
    resourceOptions.value = []
    return
  }
  resourcesLoading.value = true
  try {
    resourceOptions.value = await fetchMetadataResources(form.resourceType)
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
  if (type === 'availability') {
    return {
      check_type: 'heartbeat',
      timeout_seconds: 300,
      duration_seconds: 300,
    }
  }
  if (type === 'system') {
    return {
      check_type: 'service_health',
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
  Object.assign(form.triggerRule, defaultTriggerRule(type))
}

watch(
  () => form.type,
  (type, prev) => {
    if (type === prev) return
    applyDefaultTriggerRule(type)
    void loadMetricKeys()
    if (!monitoringResourcesRequired.value) {
      formRef.value?.clearValidate('resourceIds')
    }
    void nextTick(() => formRef.value?.clearValidate(
      (triggerValidationSpecs[prev] || []).map((spec) => spec.path),
    ))
  },
)

watch(
  () => [form.resourceType, form.scope] as const,
  () => {
    void loadMetricKeys()
    void loadResources()
    if (!monitoringResourcesRequired.value) {
      formRef.value?.clearValidate('resourceIds')
    }
  },
)

watch(
  () => eventCategory.value,
  (category) => {
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
  await loadMetadata()
  await loadMetricKeys()
  await loadResources()
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
            {{ t('ops.alertsCenter.policies.createSubtitle') }}
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
                <p class="fullscreen-form-section__subtitle">
                  {{ t('ops.alertsCenter.editor.sectionBasicDesc') }}
                </p>
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
                      {{ t('ops.alertsCenter.editor.fieldTypeHint') }}
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
                    :label="t('ops.alertsCenter.common.status')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch
                      v-model="form.enabled"
                      :active-text="t('ops.alertsCenter.common.enabled')"
                      :inactive-text="t('ops.alertsCenter.common.disabled')"
                    />
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
                    :placeholder="t('ops.alertsCenter.policies.description')"
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
                <p class="fullscreen-form-section__subtitle">
                  {{ t('ops.alertsCenter.editor.sectionTargetDesc') }}
                </p>
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
                        v-for="opt in resourceTypeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldResourceTypeHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.policies.scope')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.scope"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in scopeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldScopeHint') }}
                    </p>
                  </el-form-item>
                </div>
                <el-form-item
                  v-if="form.scope === 'selected' && form.type !== 'event'"
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
                      :placeholder="t('ops.alertsCenter.editor.fieldMonitoringResources')"
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
                <p class="fullscreen-form-section__subtitle">
                  {{ t('ops.alertsCenter.editor.sectionTriggerDesc') }}
                </p>

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
                      allow-create
                      default-first-option
                    >
                      <el-option
                        v-for="key in metricKeys"
                        :key="key"
                        :value="key"
                        :label="key"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldMetricHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldOperator')"
                    prop="triggerRule.operator"
                    data-alert-policy-field="operator"
                  >
                    <el-select
                      v-model="metricOperator"
                      class="w-full"
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
                    <el-input-number
                      v-model="metricThreshold"
                      class="w-full"
                      :min="0"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldThresholdHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldUnit')">
                    <el-input v-model="metricUnit" />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldUnitHint') }}
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
                    >
                      <el-option
                        v-for="item in taskTypes"
                        :key="item"
                        :value="item"
                        :label="item"
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
                    >
                      <el-option
                        v-for="item in taskEventTypes"
                        :key="item"
                        :value="item"
                        :label="item"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.eventTypeHint') }}
                    </p>
                  </el-form-item>
                </div>

                <div
                  v-else-if="form.type === 'availability'"
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
                    >
                      <el-option
                        v-for="item in availabilityCheckTypes"
                        :key="item"
                        :value="item"
                        :label="item"
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
                      v-model="availabilityDuration"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in durationOptions"
                        :key="`avail-${opt.value}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldDurationHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.fieldAvailabilityTimeout')"
                    prop="triggerRule.timeout_seconds"
                    data-alert-policy-field="timeout_seconds"
                  >
                    <el-input-number
                      v-model="availabilityTimeout"
                      class="w-full"
                      :min="0"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldAvailabilityTimeoutHint') }}
                    </p>
                  </el-form-item>
                </div>

                <div
                  v-else-if="form.type === 'system'"
                  class="fullscreen-form-grid"
                >
                  <el-form-item
                    :label="t('ops.alertsCenter.editor.checkType')"
                    prop="triggerRule.check_type"
                    data-alert-policy-field="check_type"
                  >
                    <el-select
                      v-model="systemCheckType"
                      class="w-full"
                    >
                      <el-option
                        v-for="item in systemCheckTypes"
                        :key="item"
                        :value="item"
                        :label="item"
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
                      v-model="systemDuration"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in durationOptions"
                        :key="`sys-${opt.value}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldDurationHint') }}
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
                    >
                      <el-option
                        v-for="item in eventCategories"
                        :key="item"
                        :value="item"
                        :label="item"
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
                      multiple
                      filterable
                      collapse-tags
                      collapse-tags-tooltip
                      :max-collapse-tags="1"
                      :tag-tooltip="{ popperClass: 'alert-policy-editor-event-types-tooltip', placement: 'bottom' }"
                      class="w-full alert-policy-editor-event-types"
                    >
                      <el-option
                        v-for="item in eventTypesByCategory[eventCategory] || []"
                        :key="item"
                        :value="item"
                        :label="item"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.eventTypesHint') }}
                    </p>
                  </el-form-item>
                </div>
              </section>

              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.alertsCenter.editor.sectionRecovery') }}
                </h3>
                <p class="fullscreen-form-section__subtitle">
                  {{ t('ops.alertsCenter.editor.sectionRecoveryDesc') }}
                </p>
                <el-form-item class="fullscreen-form-status-item">
                  <el-switch
                    v-model="recoveryEnabled"
                    :active-text="t('ops.alertsCenter.editor.fieldRecoveryEnabled')"
                  />
                </el-form-item>
                <p class="fullscreen-form-field__hint">
                  {{ t('ops.alertsCenter.editor.fieldRecoveryEnabledHint') }}
                </p>
                <div
                  v-if="recoveryEnabled"
                  class="fullscreen-form-grid mt-3"
                >
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldRecoveryCondition')">
                    <el-select
                      v-model="recoveryCondition"
                      class="w-full"
                    >
                      <el-option
                        value="below_threshold"
                        :label="t('ops.alertsCenter.editor.conditionBelowThreshold')"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldRecoveryConditionHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldOperator')">
                    <el-select
                      v-model="recoveryOperator"
                      class="w-full"
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
                    <el-input-number
                      v-model="recoveryThreshold"
                      class="w-full"
                      :min="0"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldRecoveryThresholdHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldRecoveryDuration')">
                    <el-select
                      v-model="recoveryDuration"
                      class="w-full"
                    >
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
                <p class="fullscreen-form-section__subtitle">
                  {{ t('ops.alertsCenter.editor.sectionNotificationDesc') }}
                </p>
                <div class="fullscreen-form-grid">
                  <el-form-item :label="t('ops.alertsCenter.policies.notificationChannels')">
                    <el-select
                      v-model="form.notificationChannelIds"
                      multiple
                      class="w-full"
                      filterable
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
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldNotifyOnFiring')">
                    <el-select
                      v-model="notifyOnFiring"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in yesNoOptions"
                        :key="`firing-${opt.label}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldNotifyOnFiringHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldNotifyOnResolved')">
                    <el-select
                      v-model="notifyOnResolved"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in yesNoOptions"
                        :key="`resolved-${opt.label}`"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.alertsCenter.editor.fieldNotifyOnResolvedHint') }}
                    </p>
                  </el-form-item>
                  <el-form-item :label="t('ops.alertsCenter.editor.fieldRepeatInterval')">
                    <el-input-number
                      v-model="repeatIntervalSeconds"
                      class="w-full"
                      :min="0"
                      :step="60"
                    />
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
                      :active-text="t('ops.alertsCenter.common.enabled')"
                      :inactive-text="t('ops.alertsCenter.common.disabled')"
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
                    <el-input-number
                      v-model="mergeWindowSeconds"
                      class="w-full"
                      :min="60"
                      :step="60"
                    />
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
              :disabled="saving"
              @click="savePolicy"
            >
              {{ t('common.save') }}
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
                  {{ t('ops.alertsCenter.editor.previewTitle') }}
                </h5>
                <div
                  v-for="row in previewBasicRows"
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
                  {{ t('ops.alertsCenter.editor.sectionTrigger') }}
                </h5>
                <div
                  v-for="row in previewRuleRows"
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
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('ops.alertsCenter.editor.previewNotification') }}</span>
                  <span
                    class="add-form-preview-row__value add-form-preview-row__value--badge"
                    :class="{ 'add-form-preview-row__value--empty': !hasPreviewChannels }"
                  >
                    <span
                      v-if="hasPreviewChannels"
                      class="add-form-preview-row__dot add-form-preview-row__dot--primary"
                    />
                    {{ previewChannels }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('ops.alertsCenter.common.status') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="form.enabled ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'"
                  >
                    {{ form.enabled ? t('ops.alertsCenter.editor.valueEnabled') : t('ops.alertsCenter.editor.valueDisabled') }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('ops.alertsCenter.editor.previewNotifyTimingLabel') }}</span>
                  <span class="add-form-preview-row__value">
                    {{ t('ops.alertsCenter.editor.previewNotifyTiming', {
                      firing: boolLabel(form.recoveryRule.notify_on_firing),
                      resolved: boolLabel(form.recoveryRule.notify_on_resolved),
                    }) }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('ops.alertsCenter.editor.fieldRepeatInterval') }}</span>
                  <span class="add-form-preview-row__value">{{ silenceLabel(form.triggerRule.repeat_interval_seconds) }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('ops.alertsCenter.editor.fieldMergeNotification') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="form.triggerRule.merge_enabled ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'"
                  >
                    {{ form.triggerRule.merge_enabled ? t('ops.alertsCenter.editor.valueEnabled') : t('ops.alertsCenter.editor.valueDisabled') }}
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
