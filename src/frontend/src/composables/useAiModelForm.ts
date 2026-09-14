import { computed, reactive, ref, watch, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage, apiErrorMessageI18n } from '../lib/api'
import { openErrorDetails } from '../lib/errors/details'
import { aiProviderLabel } from '../lib/aiProviderDisplay'
import { defaultAiModelDisplayName } from '../lib/aiModelDisplay'
import {
  capabilityClass,
  normalizeCapabilities,
  type AiCatalogCapability,
} from '../lib/aiModelCapabilities'
import {
  createLensModel,
  fetchLensModelCatalog,
  fetchLensModelDetail,
  fetchLensModelProviders,
  testSavedLensModel,
  testLensModel,
  updateLensModel,
} from '../lib/lensApi'

export type AiCatalogModel = {
  id: string
  label?: string
  name?: string
  capabilities?: AiCatalogCapability[]
  max_input_tokens?: number
  max_output_tokens?: number
  reference_pricing?: {
    input_usd_per_1m?: number | null
    output_usd_per_1m?: number | null
  } | null
}

export type AiCatalogProvider = {
  id: string
  label?: string
  name?: string
  default_api_base?: string | null
  models?: AiCatalogModel[]
}

export type AiModelConnectionTestSummary = {
  provider: string
  model: string
  endpoint: string
  durationMs: number
}

type AiModelConnectionSettings = {
  provider: string
  model: string
  apiBase: string
  isActive: boolean
  advancedSignature: string
}

type ProviderSchemaEntry = {
  default_api_base?: string
  default_model?: string
  required?: string[]
  optional?: string[]
  editable_params?: string[]
  [key: string]: unknown
}

type AiModelAdvancedValue = string | number | boolean | null

export type AiModelAdvancedParameter = {
  name: string
  label: string
  inputType: 'boolean' | 'number' | 'text'
  required: boolean
  defaultValue?: string | number | boolean | null
  min?: number
  max?: number
  step?: number
  integer?: boolean
}

const CORE_CONFIG_PARAMETERS = new Set(['api_base', 'api_key', 'model'])
const OPENAI_COMPATIBLE_PARAMETERS = [
  'api_base',
  'api_key',
  'model',
  'max_tokens',
  'temperature',
  'top_p',
  'request_timeout_seconds',
  'num_retries',
  'vision',
]
const ADVANCED_PARAMETER_LABEL_KEYS: Record<string, string> = {
  api_version: 'apiVersion',
  deployment: 'deployment',
  max_tokens: 'maxOutputTokens',
  num_retries: 'numRetries',
  request_timeout_seconds: 'requestTimeoutSeconds',
  temperature: 'temperature',
  top_p: 'topP',
  vision: 'visionCapability',
}
const BOOLEAN_PARAMETERS = new Set(['vision'])
const ADVANCED_NUMERIC_RULES: Record<
  string,
  { min?: number; max?: number; step?: number; integer?: boolean }
> = {
  max_tokens: { min: 1, step: 1 },
  num_retries: { min: 0, max: 10, step: 1, integer: true },
  request_timeout_seconds: { min: 1, step: 1 },
  temperature: { min: 0, max: 2, step: 0.1 },
  top_p: { min: 0, max: 1, step: 0.1 },
}
function isConfiguredValue(value: unknown) {
  return value !== null && value !== undefined && value !== ''
}

function humanizeParameterName(parameter: string) {
  return parameter
    .split('_')
    .filter(Boolean)
    .map((part) => {
      const normalized = part.toLowerCase()
      if (normalized === 'api') return 'API'
      if (normalized === 'id') return 'ID'
      if (normalized === 'url') return 'URL'
      return `${part.charAt(0).toUpperCase()}${part.slice(1)}`
    })
    .join(' ')
}

export function aiModelReferencePriceLine(
  pricing: AiCatalogModel['reference_pricing'],
  labels: { input: string; output: string },
  locale = 'en-US',
) {
  if (!pricing) return ''
  const formatPrice = (value: unknown) => (
    typeof value === 'number' && Number.isFinite(value)
      ? new Intl.NumberFormat(locale, {
          minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
          maximumFractionDigits: 2,
        }).format(value)
      : ''
  )
  const inputPrice = formatPrice(pricing.input_usd_per_1m)
  const outputPrice = formatPrice(pricing.output_usd_per_1m)
  const input = !inputPrice
    ? ''
    : `$${inputPrice}/1M ${labels.input}`
  const output = !outputPrice
    ? ''
    : `$${outputPrice}/1M ${labels.output}`
  return [input, output].filter(Boolean).join(' · ')
}

function hasReferencePrice(pricing: AiCatalogModel['reference_pricing']) {
  return Boolean(
    pricing &&
    [pricing.input_usd_per_1m, pricing.output_usd_per_1m]
      .some((value) => typeof value === 'number' && Number.isFinite(value)),
  )
}

export function aiModelConnectionTestSucceeded(result: unknown) {
  if (!result || typeof result !== 'object') return false
  const payload = result as { ok?: unknown; success?: unknown }
  if ('ok' in payload) return payload.ok === true
  if ('success' in payload) return payload.success === true
  return false
}

export function aiModelConnectionTestFailureDetail(result: unknown, fallback: string) {
  if (!result || typeof result !== 'object') return fallback
  const payload = result as { message?: unknown; detail?: unknown; error?: unknown }
  for (const value of [payload.message, payload.detail, payload.error]) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return fallback
}

function providerDisplayName(row: AiCatalogProvider) {
  const canonicalLabel = aiProviderLabel(row.id, row.id)
  return canonicalLabel !== row.id
    ? canonicalLabel
    : row.label || row.name || canonicalLabel
}

export function useAiModelForm(editingUuid: Ref<string | null>) {
  const { t } = useI18n()

  const loading = ref(false)
  const saving = ref(false)
  const testing = ref(false)
  const testOk = ref<boolean | null>(null)
  const testDetail = ref('')
  const testSummary = ref<AiModelConnectionTestSummary | null>(null)
  const modelDropdownOpen = ref(false)
  const useCustomModel = ref(false)
  const nameTouched = ref(false)
  const initialConnectionSettings = ref<AiModelConnectionSettings | null>(null)
  const initiallyConfiguredAdvancedParameters = ref(new Set<string>())
  const preservedProviderConfig = ref<Record<string, unknown>>({})

  const providers = ref<AiCatalogProvider[]>([])
  const capabilityLabels = ref<Record<string, string>>({})
  const providerSchemas = ref<Record<string, ProviderSchemaEntry>>({})

  const form = reactive({
    name: '',
    provider: '',
    model: '',
    api_key: '',
    api_base: '',
    advanced: {} as Record<string, AiModelAdvancedValue>,
    is_active: true,
  })

  const isEditing = computed(() => Boolean(editingUuid.value))

  const currentProvider = computed(() =>
    providers.value.find((p) => p.id === form.provider) ?? null,
  )

  const currentProviderModels = computed(() => currentProvider.value?.models ?? [])

  const currentProviderSchema = computed(() => providerSchemas.value[form.provider] ?? null)

  function advancedParameterNames(providerId: string) {
    const editable = providerSchemas.value[providerId]?.editable_params
    const parameterNames = providerId === 'openai_compatible'
      ? [
          ...OPENAI_COMPATIBLE_PARAMETERS,
          ...(Array.isArray(editable) ? editable : []),
        ]
      : Array.isArray(editable)
        ? editable
        : []
    return [...new Set(parameterNames.filter((name) => (
      typeof name === 'string' && name && !CORE_CONFIG_PARAMETERS.has(name)
    )))]
  }

  function parameterLabel(parameter: string) {
    const labelKey = ADVANCED_PARAMETER_LABEL_KEYS[parameter]
    return labelKey
      ? t(`insight.aiSettings.${labelKey}`)
      : humanizeParameterName(parameter)
  }

  const advancedParameters = computed<AiModelAdvancedParameter[]>(() => {
    const schema = currentProviderSchema.value
    const required = new Set(schema?.required ?? [])
    return advancedParameterNames(form.provider).map((name) => {
      const numericRule = ADVANCED_NUMERIC_RULES[name]
      const providerDefault = schema?.[`default_${name}`]
      const defaultValue = providerDefault !== null && providerDefault !== undefined
        ? providerDefault
        : null
      return {
        name,
        label: parameterLabel(name),
        inputType: BOOLEAN_PARAMETERS.has(name) ? 'boolean' : numericRule ? 'number' : 'text',
        required: required.has(name),
        defaultValue: typeof defaultValue === 'string' || typeof defaultValue === 'number' || typeof defaultValue === 'boolean'
          ? defaultValue
          : null,
        ...numericRule,
      }
    })
  })

  const apiBaseRequired = computed(() => (
    form.provider === 'openai_compatible' ||
    (currentProviderSchema.value?.required?.includes('api_base') ?? false)
  ))

  const selectedModelInfo = computed(() => {
    const modelId = form.model.trim()
    if (!modelId) return null
    const row = currentProviderModels.value.find((m) => m.id === modelId)
    if (!row) return null
    return {
      capabilities: normalizeCapabilities(row.capabilities),
      max_input_tokens: row.max_input_tokens,
      max_output_tokens: row.max_output_tokens,
      reference_pricing: row.reference_pricing,
    }
  })

  const hasSelectedModelInfo = computed(() => Boolean(
    selectedModelInfo.value && (
      selectedModelInfo.value.capabilities.length ||
      selectedModelInfo.value.max_input_tokens ||
      selectedModelInfo.value.max_output_tokens ||
      hasReferencePrice(selectedModelInfo.value.reference_pricing)
    ),
  ))

  const modelSelectLabel = computed(() => {
    if (useCustomModel.value) {
      return form.model.trim()
        ? `${t('insight.aiSettings.modelCustom')} · ${form.model.trim()}`
        : t('insight.aiSettings.modelCustom')
    }
    const row = currentProviderModels.value.find((m) => m.id === form.model)
    return row?.label || row?.name || row?.id || t('insight.aiSettings.modelSelect')
  })

  function capabilityLabel(key: string) {
    return capabilityLabels.value[key] || key
  }

  function suggestedDisplayName() {
    const providerRow = currentProvider.value
    const providerLabel = providerRow ? providerDisplayName(providerRow) : aiProviderLabel(form.provider, form.provider)
    const modelId = form.model.trim()
    if (!providerLabel && !modelId) return ''
    if (!modelId) return providerLabel
    if (useCustomModel.value) {
      return defaultAiModelDisplayName(form.provider, modelId, providerLabel, modelId)
    }
    const row = currentProviderModels.value.find((m) => m.id === modelId)
    const modelLabel = row?.label || row?.name || modelId
    return defaultAiModelDisplayName(form.provider, modelId, providerLabel, modelLabel)
  }

  function syncSuggestedName() {
    if (nameTouched.value) return
    form.name = suggestedDisplayName()
  }

  function onNameInput() {
    nameTouched.value = true
  }

  function resetNameAutoFill() {
    nameTouched.value = false
    syncSuggestedName()
  }

  function resetForm() {
    form.name = ''
    form.provider = providers.value[0]?.id || ''
    form.model = ''
    form.api_key = ''
    form.api_base = ''
    preservedProviderConfig.value = {}
    form.is_active = true
    useCustomModel.value = false
    nameTouched.value = false
    testOk.value = null
    testDetail.value = ''
    testSummary.value = null
    initialConnectionSettings.value = null
    initiallyConfiguredAdvancedParameters.value = new Set()
    resetAdvancedValues(form.provider)
    applyProviderDefaults(form.provider)
    syncSuggestedName()
  }

  function resetAdvancedValues(providerId: string, config: Record<string, unknown> = {}) {
    for (const parameter of Object.keys(form.advanced)) delete form.advanced[parameter]
    for (const parameter of advancedParameterNames(providerId)) {
      const value = config[parameter]
      if (BOOLEAN_PARAMETERS.has(parameter)) {
        form.advanced[parameter] = value === true
        continue
      }
      if (!isConfiguredValue(value)) {
        form.advanced[parameter] = ADVANCED_NUMERIC_RULES[parameter] ? null : ''
        continue
      }
      if (ADVANCED_NUMERIC_RULES[parameter]) {
        const numericValue = Number(value)
        form.advanced[parameter] = Number.isFinite(numericValue) ? numericValue : null
      } else {
        form.advanced[parameter] = String(value)
      }
    }
  }

  function applyProviderDefaults(providerId: string) {
    if (!providerId || isEditing.value) return
    const schema = providerSchemas.value[providerId]
    const provider = providers.value.find((p) => p.id === providerId)
    const defaultBase = provider?.default_api_base || schema?.default_api_base || ''
    form.api_base = defaultBase
    if (schema?.default_model && !form.model) form.model = schema.default_model
  }

  function selectProvider(providerId: string) {
    if (isEditing.value) return
    form.provider = providerId
    form.model = ''
    preservedProviderConfig.value = {}
    resetAdvancedValues(providerId)
    useCustomModel.value = false
    testOk.value = null
    testDetail.value = ''
    testSummary.value = null
    applyProviderDefaults(providerId)
    resetNameAutoFill()
  }

  function selectModel(modelId: string) {
    modelDropdownOpen.value = false
    if (modelId === '__custom__') {
      useCustomModel.value = true
      form.model = ''
      resetNameAutoFill()
      return
    }
    useCustomModel.value = false
    form.model = modelId
    resetNameAutoFill()
  }

  async function loadCatalog() {
    const [catalogRaw, providersRaw] = await Promise.all([
      fetchLensModelCatalog(),
      fetchLensModelProviders().catch(() => null),
    ])

    if (catalogRaw && typeof catalogRaw === 'object') {
      const catalog = catalogRaw as {
        providers?: AiCatalogProvider[]
        capability_labels?: Record<string, string>
      }
      providers.value = (catalog.providers ?? []).map((row) => ({
        ...row,
        label: providerDisplayName(row),
        models: (row.models ?? []).map((m) => ({
          ...m,
          label: m.label || m.name || m.id,
        })),
      }))
      capabilityLabels.value = catalog.capability_labels ?? {}
    } else if (Array.isArray(catalogRaw)) {
      providers.value = catalogRaw as AiCatalogProvider[]
    } else {
      providers.value = []
    }

    if (providersRaw && typeof providersRaw === 'object') {
      const schemaObj = providersRaw as { providers?: Record<string, ProviderSchemaEntry> }
      if (schemaObj.providers && typeof schemaObj.providers === 'object') {
        providerSchemas.value = schemaObj.providers
      } else if (Array.isArray(providersRaw)) {
        providerSchemas.value = Object.fromEntries(
          (providersRaw as { id: string }[]).map((row) => [row.id, {}]),
        )
      }
    }
  }

  async function loadEditing() {
    if (!editingUuid.value) {
      resetForm()
      return
    }
    const detail = await fetchLensModelDetail(editingUuid.value)
    form.provider = detail.provider || ''
    form.model = detail.config?.model || ''
    form.name = detail.name?.trim() || ''
    form.api_key = ''
    form.api_base = detail.config?.api_base || ''
    const detailConfig = detail.config && typeof detail.config === 'object'
      ? detail.config as Record<string, unknown>
      : {}
    resetAdvancedValues(form.provider, detailConfig)
    const editableAdvanced = new Set(advancedParameterNames(form.provider))
    initiallyConfiguredAdvancedParameters.value = new Set(
      [...editableAdvanced].filter((name) => (
        Object.prototype.hasOwnProperty.call(detailConfig, name)
      )),
    )
    preservedProviderConfig.value = Object.fromEntries(
      Object.entries(detailConfig).filter(([name]) => (
        !CORE_CONFIG_PARAMETERS.has(name) && !editableAdvanced.has(name)
      )),
    )
    form.is_active = detail.is_active !== false
    initialConnectionSettings.value = {
      provider: form.provider,
      model: form.model,
      apiBase: form.api_base,
      isActive: form.is_active,
      advancedSignature: advancedSettingsSignature(),
    }
    nameTouched.value = Boolean(form.name)
    if (!form.name) syncSuggestedName()
    const inList = currentProviderModels.value.some((m) => m.id === form.model)
    useCustomModel.value = Boolean(form.model) && !inList
  }

  function modelCapabilities(model: AiCatalogModel) {
    return normalizeCapabilities(model.capabilities)
  }

  function updateAdvancedParameter(
    parameter: string,
    value: string | number | boolean | undefined,
  ) {
    if (BOOLEAN_PARAMETERS.has(parameter)) {
      form.advanced[parameter] = value === true
      return
    }
    if (value === undefined || value === '') {
      form.advanced[parameter] = null
      return
    }
    if (ADVANCED_NUMERIC_RULES[parameter]) {
      const numericValue = Number(value)
      form.advanced[parameter] = Number.isFinite(numericValue) ? numericValue : null
      return
    }
    form.advanced[parameter] = String(value)
  }

  watch(
    () => form.provider,
    (next, prev) => {
      if (!next || next === prev || isEditing.value) return
      form.model = ''
      preservedProviderConfig.value = {}
      resetAdvancedValues(next)
      useCustomModel.value = false
      testOk.value = null
      testDetail.value = ''
      testSummary.value = null
      applyProviderDefaults(next)
      resetNameAutoFill()
    },
  )

  watch(
    () => form.model,
    () => {
      if (isEditing.value && nameTouched.value) return
      syncSuggestedName()
    },
  )

  watch(
    () => [form.provider, form.model, form.api_key, form.api_base, JSON.stringify(form.advanced)],
    () => {
      testOk.value = null
      testDetail.value = ''
      testSummary.value = null
    },
  )

  async function init() {
    loading.value = true
    try {
      await loadCatalog()
      await loadEditing()
      if (!editingUuid.value && !form.provider && providers.value[0]) {
        selectProvider(providers.value[0].id)
      }
    } catch (err) {
      ElMessage.error({ message: apiErrorMessage(err, t('errors.generic.loadFailed')), grouping: true })
    } finally {
      loading.value = false
    }
  }

  function buildConfigPayload(includeEmptyAdvanced = false) {
    const config: Record<string, unknown> = {
      ...preservedProviderConfig.value,
      model: form.model.trim(),
      api_base: form.api_base.trim(),
    }
    if (form.api_key.trim()) config.api_key = form.api_key.trim()
    for (const parameter of advancedParameters.value) {
      const value = form.advanced[parameter.name]
      const includeBoolean = parameter.inputType === 'boolean' && (
        value === true || initiallyConfiguredAdvancedParameters.value.has(parameter.name)
      )
      if (includeBoolean || (parameter.inputType !== 'boolean' && isConfiguredValue(value))) {
        config[parameter.name] = value
      } else if (
        includeEmptyAdvanced &&
        initiallyConfiguredAdvancedParameters.value.has(parameter.name)
      ) {
        config[parameter.name] = null
      }
    }
    return config
  }

  function advancedSettingsSignature() {
    return JSON.stringify(
      Object.fromEntries(
        advancedParameters.value
          .map(({ name }) => [name, form.advanced[name]])
          .filter(([name, value]) => (
            value === true ||
            (value === false && initiallyConfiguredAdvancedParameters.value.has(String(name))) ||
            (typeof value !== 'boolean' && isConfiguredValue(value))
          )),
      ),
    )
  }

  function buildPayload(includeEmptyAdvanced = false) {
    return {
      name: form.name.trim(),
      provider: form.provider,
      config: buildConfigPayload(includeEmptyAdvanced),
      is_active: form.is_active,
    }
  }

  function connectionSettingsChanged() {
    const initial = initialConnectionSettings.value
    if (!initial) return false
    return (
      form.provider !== initial.provider ||
      form.model.trim() !== initial.model.trim() ||
      form.api_base.trim() !== initial.apiBase.trim() ||
      advancedSettingsSignature() !== initial.advancedSignature ||
      Boolean(form.api_key.trim())
    )
  }

  const apiKeyRequiredForSave = computed(() => {
    if (!editingUuid.value) return true
    return Boolean(
      form.is_active &&
      (connectionSettingsChanged() || initialConnectionSettings.value?.isActive === false),
    )
  })

  function buildUpdatePayload() {
    const initial = initialConnectionSettings.value
    if (!initial) return buildPayload()
    const payload: Record<string, unknown> = { name: form.name.trim() }
    if (connectionSettingsChanged()) {
      payload.provider = form.provider
      payload.config = buildConfigPayload(true)
      payload.is_active = form.is_active
    } else if (form.is_active !== initial.isActive) {
      payload.is_active = form.is_active
    }
    return payload
  }

  function currentConnectionTestSummary(): Omit<AiModelConnectionTestSummary, 'durationMs'> {
    const provider = currentProvider.value
    const providerLabel = provider ? providerDisplayName(provider) : form.provider
    const schema = providerSchemas.value[form.provider]
    return {
      provider: providerLabel,
      model: form.model.trim(),
      endpoint:
        form.api_base.trim() ||
        provider?.default_api_base ||
        schema?.default_api_base ||
        t('insight.aiSettings.connectionTestDefaultEndpoint'),
    }
  }

  function connectionSettingsSignature() {
    return JSON.stringify([
      form.provider,
      form.model.trim(),
      form.api_key,
      form.api_base.trim(),
      advancedSettingsSignature(),
    ])
  }

  function validateProviderParameters() {
    if (apiBaseRequired.value && !form.api_base.trim()) {
      ElMessage.warning({
        message: t('insight.aiSettings.requiredProviderField', {
          field: t('insight.aiSettings.labelApiBase'),
        }),
        grouping: true,
      })
      return false
    }
    for (const parameter of advancedParameters.value) {
      const value = form.advanced[parameter.name]
      if (parameter.required && !isConfiguredValue(value)) {
        ElMessage.warning({
          message: t('insight.aiSettings.requiredProviderField', { field: parameter.label }),
          grouping: true,
        })
        return false
      }
      if (!isConfiguredValue(value) || parameter.inputType !== 'number') continue
      const numericValue = Number(value)
      if (
        !Number.isFinite(numericValue) ||
        (parameter.integer === true && !Number.isInteger(numericValue)) ||
        (parameter.min !== undefined && numericValue < parameter.min) ||
        (parameter.max !== undefined && numericValue > parameter.max)
      ) {
        ElMessage.warning({
          message: t('insight.aiSettings.invalidProviderField', { field: parameter.label }),
          grouping: true,
        })
        return false
      }
    }
    return true
  }

  function canTestSavedConfiguration() {
    return Boolean(
      editingUuid.value &&
      initialConnectionSettings.value?.isActive !== false &&
      !connectionSettingsChanged(),
    )
  }

  async function runTest() {
    if (!form.provider || !form.model.trim()) {
      ElMessage.warning({ message: t('insight.aiSettings.testNeedModel'), grouping: true })
      return false
    }
    if (!validateProviderParameters()) return false
    const testSavedConfiguration = canTestSavedConfiguration()
    if (!testSavedConfiguration && !form.api_key.trim()) {
      const messageKey = !editingUuid.value
        ? 'insight.aiSettings.apiKeyRequired'
        : initialConnectionSettings.value?.isActive === false
          ? 'insight.aiSettings.apiKeyRequiredForActivation'
          : 'insight.aiSettings.apiKeyRequiredForConnectionChange'
      ElMessage.warning({ message: t(messageKey), grouping: true })
      return false
    }
    testing.value = true
    testOk.value = null
    testDetail.value = ''
    testSummary.value = null
    const startedAt = Date.now()
    const summary = currentConnectionTestSummary()
    const testedSettings = connectionSettingsSignature()
    try {
      const res = testSavedConfiguration
        ? await testSavedLensModel(editingUuid.value!)
        : await testLensModel(buildPayload())
      if (testedSettings !== connectionSettingsSignature()) return false
      const ok = aiModelConnectionTestSucceeded(res)
      testOk.value = ok
      testSummary.value = {
        ...summary,
        durationMs: Math.max(0, Date.now() - startedAt),
      }
      testDetail.value = ok
        ? t('insight.aiSettings.connectionTestSuccessDescription', {
            provider: summary.provider,
            model: summary.model,
          })
        : aiModelConnectionTestFailureDetail(
            res,
            t('insight.aiSettings.connectionTestFailureFallback'),
          )
      if (ok) {
        ElMessage.success({ message: t('insight.aiSettings.connectivityOk'), grouping: true })
      } else {
        openErrorDetails({
          error: res,
          overrides: {
            title: t('insight.aiSettings.connectionTestFailureTitle'),
            summary: testDetail.value,
            issue: testDetail.value,
            rawDetail: res,
          },
        })
      }
      return ok
    } catch (err) {
      if (testedSettings !== connectionSettingsSignature()) return false
      testOk.value = false
      testSummary.value = {
        ...summary,
        durationMs: Math.max(0, Date.now() - startedAt),
      }
      testDetail.value = apiErrorMessageI18n(
        err,
        t,
        t('insight.aiSettings.connectionTestFailureFallback'),
      )
      openErrorDetails({
        error: err,
        overrides: {
          title: t('insight.aiSettings.connectionTestFailureTitle'),
          summary: testDetail.value,
          issue: testDetail.value,
        },
      })
      return false
    } finally {
      testing.value = false
    }
  }

  async function submit() {
    if (!form.provider || !form.model.trim()) {
      ElMessage.warning({ message: t('insight.aiSettings.formRequired'), grouping: true })
      return false
    }
    if (!validateProviderParameters()) return false
    if (!form.name.trim()) {
      syncSuggestedName()
    }
    if (!form.name.trim()) {
      ElMessage.warning({ message: t('insight.aiSettings.nameRequired'), grouping: true })
      return false
    }
    if (!editingUuid.value && !form.api_key.trim()) {
      ElMessage.warning({ message: t('insight.aiSettings.apiKeyRequired'), grouping: true })
      return false
    }
    if (editingUuid.value && apiKeyRequiredForSave.value && !form.api_key.trim()) {
      ElMessage.warning({
        message: t(
          initialConnectionSettings.value?.isActive === false
            ? 'insight.aiSettings.apiKeyRequiredForActivation'
            : 'insight.aiSettings.apiKeyRequiredForConnectionChange',
        ),
        grouping: true,
      })
      return false
    }
    saving.value = true
    try {
      if (editingUuid.value) {
        await updateLensModel(editingUuid.value, buildUpdatePayload())
      } else {
        await createLensModel(buildPayload())
      }
      ElMessage.success({ message: t('insight.aiSettings.saveSuccess'), grouping: true })
      return true
    } catch (err) {
      ElMessage.error({
        message: apiErrorMessageI18n(err, t, t('errors.generic.requestFailed')),
        grouping: true,
      })
      return false
    } finally {
      saving.value = false
    }
  }

  return {
    loading,
    saving,
    testing,
    testOk,
    testDetail,
    testSummary,
    apiKeyRequiredForSave,
    modelDropdownOpen,
    useCustomModel,
    providers,
    form,
    isEditing,
    currentProvider,
    currentProviderModels,
    advancedParameters,
    apiBaseRequired,
    selectedModelInfo,
    hasSelectedModelInfo,
    modelSelectLabel,
    capabilityLabel,
    capabilityClass,
    selectProvider,
    selectModel,
    onNameInput,
    init,
    runTest,
    submit,
    modelCapabilities,
    updateAdvancedParameter,
  }
}
