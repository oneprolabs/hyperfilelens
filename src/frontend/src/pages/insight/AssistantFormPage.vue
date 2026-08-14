<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  lensAssistantsPath,
  lensKnowledgePath,
  lensMcpPath,
  lensModelsPath,
  lensSkillsPath,
} from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Plus, RefreshCw } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import { routeLocationWithListRefresh } from '../../lib/listRouteRefresh'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import {
  createLensAssistant,
  fetchLensAssistant,
  fetchLensAssistantFormOptions,
  listLensModels,
  updateLensAssistant,
  type LensAssistantFormOptions,
  type LensAssistantKnowledgeSourceOption,
  type LensLlmConfig,
} from '../../lib/lensApi'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const DEFAULT_EXCLUDE_EXTENSIONS = '.lock,.pyc,.sqlite3'
const DEFAULT_EXCLUDE_DIRS = '.git,.venv,__pycache__,node_modules,dist,build'

const editingUuid = computed(() => {
  const raw = route.params.uuid
  return typeof raw === 'string' && raw ? raw : null
})

const isEditing = computed(() => Boolean(editingUuid.value))

const loading = ref(false)
const saving = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
const modelsRefreshing = ref(false)
const formOptionsRefreshing = ref(false)
const formOptions = ref<LensAssistantFormOptions | null>(null)
const models = ref<LensLlmConfig[]>([])

const name = ref('')
const agentModelRef = ref('')
const multimodalModelRef = ref('')
const maxConcurrency = ref(5)
const agentRounds = ref('balanced')
const knowledgeSourceId = ref<number | null>(null)
const selectedTask = ref('')
const retrievalScopeText = ref('')
const excludeExtensionsText = ref(DEFAULT_EXCLUDE_EXTENSIONS)
const excludeDirsText = ref(DEFAULT_EXCLUDE_DIRS)
const workspaceGuideOverview = ref('')
const skillUuids = ref<string[]>([])
const mcpUuids = ref<string[]>([])
const visibilityScope = ref<'user' | 'organization'>('user')

const activeModels = computed(() => models.value.filter((row) => row.is_active !== false))
const gateways = computed(() => formOptions.value?.gateways ?? [])
const knowledgeSources = computed(() => formOptions.value?.knowledge_sources ?? [])
const skills = computed(() => formOptions.value?.skills ?? [])
const mcps = computed(() => formOptions.value?.mcps ?? [])

const selectedKnowledgeSource = computed(
  () => knowledgeSources.value.find((row) => row.id === knowledgeSourceId.value) ?? null,
)

const selectableKnowledgeSources = computed(() =>
  knowledgeSources.value.filter((row) => {
    if (!row.lensnode_uuid) return false
    if (row.indexed_paths.length === 0 && row.scope_paths.length === 0) return false
    if (row.status !== 'ready' && row.status !== 'degraded') return false
    return true
  }),
)

const selectedGateway = computed(
  () =>
    gateways.value.find((row) => row.lensnode_uuid === selectedKnowledgeSource.value?.lensnode_uuid) ??
    null,
)
const taskOptions = computed(() => selectedGateway.value?.tasks ?? [])

const selectableSkills = computed(() =>
  skills.value.filter(
    (skill) => !(typeof skill.slug === 'string' && skill.slug.endsWith('-workspace-guide')),
  ),
)

const selectedSkillCount = computed(() => skillUuids.value.length)
const selectedMcpCount = computed(() => mcpUuids.value.length)

function isSkillSelected(uuid: string) {
  return skillUuids.value.includes(uuid)
}

function isMcpSelected(uuid: string) {
  return mcpUuids.value.includes(uuid)
}

const agentRoundsTiers = computed(() => [
  { value: 'flash', label: t('insight.assistants.roundsFlash'), hint: t('insight.assistants.roundsFlashHint') },
  { value: 'fast', label: t('insight.assistants.roundsFast'), hint: t('insight.assistants.roundsFastHint') },
  {
    value: 'balanced',
    label: t('insight.assistants.roundsBalanced'),
    hint: t('insight.assistants.roundsBalancedHint'),
  },
  { value: 'deep', label: t('insight.assistants.roundsDeep'), hint: t('insight.assistants.roundsDeepHint') },
  { value: 'max', label: t('insight.assistants.roundsMax'), hint: t('insight.assistants.roundsMaxHint') },
])

const pageTitle = computed(() => {
  if (isEditing.value && name.value.trim()) return name.value.trim()
  return t('insight.assistants.addPageTitle')
})

const pageDesc = computed(() => t('insight.assistants.addPageDesc'))

function knowledgeSourceStatusLabel(status: string) {
  if (status === 'ready') return t('insight.kb.statusReady')
  if (status === 'degraded') return t('insight.kb.statusDegraded')
  if (status === 'syncing') return t('insight.kb.statusSyncing')
  if (status === 'error') return t('insight.kb.statusError')
  if (status === 'paused') return t('insight.kb.statusPaused')
  return status
}

function knowledgeSourceOptionLabel(row: LensAssistantKnowledgeSourceOption) {
  return `${row.name} · ${row.gateway_name}`
}

function modelLabel(row: LensLlmConfig) {
  const provider = row.provider || row.name || 'provider'
  const model = row.config?.model || '—'
  return `${provider} · ${model}`
}

function splitList(text: string) {
  return text
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function splitLines(text: string) {
  return text
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function listToText(items: unknown) {
  if (!Array.isArray(items)) return ''
  return items.map((item) => String(item)).join(',')
}

function workspaceGuideSkillUuids(allSkills: LensAssistantFormOptions['skills']) {
  return new Set(
    allSkills
      .filter((skill) => typeof skill.slug === 'string' && skill.slug.endsWith('-workspace-guide'))
      .map((skill) => skill.uuid),
  )
}

function applyKnowledgeSourceDefaults(forceTask = false) {
  const ks = selectedKnowledgeSource.value
  if (!ks) return
  const gw = gateways.value.find((row) => row.lensnode_uuid === ks.lensnode_uuid)
  if (gw && (forceTask || !selectedTask.value) && gw.tasks[0]?.name) {
    selectedTask.value = gw.tasks[0].name
  }
}

function applyDefaults() {
  if (!agentModelRef.value && activeModels.value[0]?.uuid) {
    agentModelRef.value = activeModels.value[0].uuid
  }
  if (!knowledgeSourceId.value && selectableKnowledgeSources.value[0]?.id) {
    knowledgeSourceId.value = selectableKnowledgeSources.value[0].id
  }
  applyKnowledgeSourceDefaults()
}

function resetForm() {
  name.value = ''
  agentModelRef.value = ''
  multimodalModelRef.value = ''
  maxConcurrency.value = 5
  agentRounds.value = 'balanced'
  knowledgeSourceId.value = null
  selectedTask.value = ''
  retrievalScopeText.value = ''
  excludeExtensionsText.value = DEFAULT_EXCLUDE_EXTENSIONS
  excludeDirsText.value = DEFAULT_EXCLUDE_DIRS
  workspaceGuideOverview.value = ''
  skillUuids.value = []
  mcpUuids.value = []
  visibilityScope.value = 'user'
  applyDefaults()
}

async function loadDetail() {
  if (!editingUuid.value) return
  const row = await fetchLensAssistant(editingUuid.value)
  name.value = String(row.name || '')
  agentModelRef.value = String(row.agent_model_ref || '')
  multimodalModelRef.value = String(row.multimodal_model_ref || '')
  maxConcurrency.value = Number(row.max_concurrency ?? 5)
  agentRounds.value = String(row.agent_rounds || 'balanced')
  selectedTask.value = String(row.selected_task || '')

  const ksIdRaw = row.knowledge_source_id
  if (ksIdRaw != null && ksIdRaw !== '') {
    knowledgeSourceId.value = Number(ksIdRaw)
  } else {
    const linkedKs =
      knowledgeSources.value.find((ks) => ks.sl_assistant_uuid === editingUuid.value) ?? null
    knowledgeSourceId.value = linkedKs?.id ?? null
  }
  if (!knowledgeSourceId.value) {
    const lensnodeUuid = String(
      (row.lensnode as { uuid?: string })?.uuid || row.lensnode_uuid || row.lensnode || '',
    )
    const dirs = row.selected_dirs as { path?: string }[] | undefined
    const firstDir = dirs?.[0]?.path ? String(dirs[0].path) : ''
    const fallbackKs = knowledgeSources.value.find(
      (ks) => ks.lensnode_uuid === lensnodeUuid && ks.indexed_paths.includes(firstDir),
    )
    knowledgeSourceId.value = fallbackKs?.id ?? null
  }
  applyKnowledgeSourceDefaults(!selectedTask.value)

  const dirs = row.selected_dirs as
    | { path?: string; retrieval_scope?: { include_paths?: string[] } }[]
    | undefined
  const includePaths = new Set<string>()
  for (const dir of dirs ?? []) {
    for (const path of dir.retrieval_scope?.include_paths ?? []) {
      const normalized = String(path || '').trim()
      if (normalized) includePaths.add(normalized)
    }
  }
  retrievalScopeText.value = [...includePaths].join('\n')

  const settings = (row.settings || {}) as {
    retrieval_policy?: { exclude_extensions?: string[]; exclude_dirs?: string[] }
  }
  excludeExtensionsText.value = listToText(settings.retrieval_policy?.exclude_extensions) || DEFAULT_EXCLUDE_EXTENSIONS
  excludeDirsText.value = listToText(settings.retrieval_policy?.exclude_dirs) || DEFAULT_EXCLUDE_DIRS

  const workspaceGuide = row.workspace_guide as { content?: string } | undefined
  workspaceGuideOverview.value = String(workspaceGuide?.content || '')

  const wgUuids = workspaceGuideSkillUuids(formOptions.value?.skills ?? [])
  const skillBindings = (row.skill_bindings || []) as { skill?: { uuid?: string }; skill_uuid?: string }[]
  skillUuids.value = skillBindings
    .map((binding) => binding.skill?.uuid || binding.skill_uuid)
    .filter((uuid): uuid is string => Boolean(uuid && !wgUuids.has(uuid)))

  const mcpBindings = (row.mcp_bindings || []) as { mcp_server?: { uuid?: string }; mcp_uuid?: string }[]
  mcpUuids.value = mcpBindings
    .map((binding) => binding.mcp_server?.uuid || binding.mcp_uuid)
    .filter((uuid): uuid is string => Boolean(uuid))

  visibilityScope.value = row.visibility_scope === 'organization' ? 'organization' : 'user'
}

async function init() {
  loading.value = true
  try {
    const [modelRows, options] = await Promise.all([
      listLensModels().catch(() => [] as LensLlmConfig[]),
      fetchLensAssistantFormOptions(),
    ])
    models.value = modelRows
    formOptions.value = options
    if (isEditing.value) {
      await loadDetail()
    } else {
      resetForm()
    }
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    loading.value = false
  }
}

function handleBack() {
  router.push(routeLocationWithListRefresh(lensAssistantsPath()))
}

function openInNewTab(path: string) {
  const { href } = router.resolve(path)
  window.open(href, '_blank', 'noopener,noreferrer')
}

function openAddModel() {
  openInNewTab(lensModelsPath() + '/add')
}

function openAddKnowledgeSource() {
  openInNewTab(lensKnowledgePath() + '/add')
}

function openAddSkill() {
  openInNewTab(lensSkillsPath() + '/add')
}

function openAddMcp() {
  openInNewTab(lensMcpPath() + '/add')
}

async function refreshModels() {
  if (modelsRefreshing.value) return
  modelsRefreshing.value = true
  try {
    models.value = await listLensModels().catch(() => [] as LensLlmConfig[])
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    modelsRefreshing.value = false
  }
}

async function refreshFormOptions() {
  if (formOptionsRefreshing.value) return
  formOptionsRefreshing.value = true
  try {
    formOptions.value = await fetchLensAssistantFormOptions()
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    formOptionsRefreshing.value = false
  }
}

function buildPayload() {
  const guideContent = workspaceGuideOverview.value.trim()
  const retrievalPolicy: Record<string, string[]> = {}
  const excludeExtensions = splitList(excludeExtensionsText.value)
  const excludeDirs = splitList(excludeDirsText.value)
  if (excludeExtensions.length) retrievalPolicy.exclude_extensions = excludeExtensions
  if (excludeDirs.length) retrievalPolicy.exclude_dirs = excludeDirs
  const includePaths = splitLines(retrievalScopeText.value)

  return {
    name: name.value.trim(),
    knowledge_source_id: knowledgeSourceId.value,
    selected_task: selectedTask.value,
    retrieval_include_paths: includePaths,
    agent_model_ref: agentModelRef.value || null,
    multimodal_model_ref: multimodalModelRef.value || null,
    agent_rounds: agentRounds.value,
    max_concurrency: Number(maxConcurrency.value) || 5,
    settings: Object.keys(retrievalPolicy).length ? { retrieval_policy: retrievalPolicy } : {},
    workspace_guide: {
      enabled: Boolean(guideContent),
      content: guideContent,
    },
    skill_bindings: skillUuids.value.map((uuid) => ({ skill_uuid: uuid })),
    mcp_bindings: mcpUuids.value.map((uuid) => ({ mcp_uuid: uuid })),
    visibility_scope: visibilityScope.value,
    status: 'active',
  }
}

async function handleSubmit() {
  if (saving.value) return
  if (!validateInline([
    { field: 'name', message: t('insight.assistants.fieldName'), valid: !!name.value.trim() },
    { field: 'agentModel', message: t('insight.assistants.fieldAgentModel'), valid: !!agentModelRef.value },
    { field: 'maxConcurrency', message: t('insight.assistants.fieldMaxConcurrency'), valid: Number(maxConcurrency.value) >= 1 && Number(maxConcurrency.value) <= 50 },
    { field: 'knowledgeSource', message: t('insight.assistants.fieldKnowledgeSource'), valid: !!knowledgeSourceId.value },
    { field: 'scenario', message: t('insight.assistants.fieldScenario'), valid: !!selectedTask.value },
  ])) return
  saving.value = true
  try {
    const payload = buildPayload()
    if (isEditing.value && editingUuid.value) {
      await updateLensAssistant(editingUuid.value, payload)
      ElMessage.success(t('insight.assistants.saveSuccess'))
    } else {
      await createLensAssistant(payload)
      ElMessage.success(t('insight.assistants.createSuccess'))
    }
    router.push(routeLocationWithListRefresh(lensAssistantsPath()))
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('insight.assistants.saveFailed')))
  } finally {
    saving.value = false
  }
}

watch(knowledgeSourceId, () => {
  applyKnowledgeSourceDefaults(true)
})

watch(
  () => [route.path, route.params.uuid] as const,
  () => {
    void init()
  },
  { immediate: true },
)
</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen assistant-form-fullscreen"
  >
    <div class="fullscreen-form-page">
      <header class="fullscreen-form-header">
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
            {{ pageDesc }}
          </p>
        </div>
      </header>

      <div
        v-loading="loading"
        class="fullscreen-form-layout"
      >
        <div class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <!-- 1. Basics & Models -->
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.assistants.sectionBasics') }}
              </h3>
              <ElForm
                label-position="top"
                class="fullscreen-form-el-form"
              >
                <ElFormItem
                  data-validation-field="name"
                  :error="errors.name"
                  :label="t('insight.assistants.fieldName')"
                  required
                >
                  <ElInput
                    v-model="name"
                    maxlength="120"
                    :placeholder="t('insight.assistants.fieldNamePlaceholder')"
                    @input="clearFieldError('name')"
                  />
                  <p class="assistant-field-hint">
                    {{ t('insight.assistants.fieldNameHint') }}
                  </p>
                </ElFormItem>

                <div class="fullscreen-form-grid">
                  <ElFormItem
                    data-validation-field="agentModel"
                    :error="errors.agentModel"
                    :label="t('insight.assistants.fieldAgentModel')"
                    required
                  >
                    <div class="assistant-select-row">
                      <div class="assistant-select-row__controls">
                        <ElSelect
                          v-model="agentModelRef"
                          filterable
                          class="assistant-select-row__select"
                          :loading="modelsRefreshing"
                          :placeholder="t('insight.assistants.fieldAgentModel')"
                          @change="clearFieldError('agentModel')"
                        >
                          <ElOption
                            v-for="row in activeModels"
                            :key="row.uuid"
                            :label="modelLabel(row)"
                            :value="row.uuid"
                          />
                        </ElSelect>
                        <ElButton
                          class="hfl-refresh-button assistant-select-row__refresh"
                          :title="t('common.refresh')"
                          :aria-label="t('common.refresh')"
                          :disabled="modelsRefreshing"
                          @click="refreshModels"
                        >
                          <RefreshCw
                            :size="16"
                            :class="{ 'is-spinning': modelsRefreshing }"
                          />
                        </ElButton>
                        <ElButton
                          class="fullscreen-form-icon-btn assistant-select-row__add"
                          :title="t('insight.aiSettings.btnCreateModel')"
                          :aria-label="t('insight.aiSettings.btnCreateModel')"
                          @click="openAddModel"
                        >
                          <Plus :size="14" />
                        </ElButton>
                      </div>
                    </div>
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.modelHint') }}
                    </p>
                  </ElFormItem>
                  <ElFormItem :label="t('insight.assistants.fieldMultimodalModel')">
                    <div class="assistant-select-row">
                      <div class="assistant-select-row__controls">
                        <ElSelect
                          v-model="multimodalModelRef"
                          filterable
                          clearable
                          class="assistant-select-row__select"
                          :loading="modelsRefreshing"
                          :placeholder="t('insight.assistants.fieldMultimodalModel')"
                        >
                          <ElOption
                            v-for="row in activeModels"
                            :key="`mm-${row.uuid}`"
                            :label="modelLabel(row)"
                            :value="row.uuid"
                          />
                        </ElSelect>
                        <ElButton
                          class="hfl-refresh-button assistant-select-row__refresh"
                          :title="t('common.refresh')"
                          :aria-label="t('common.refresh')"
                          :disabled="modelsRefreshing"
                          @click="refreshModels"
                        >
                          <RefreshCw
                            :size="16"
                            :class="{ 'is-spinning': modelsRefreshing }"
                          />
                        </ElButton>
                        <ElButton
                          class="fullscreen-form-icon-btn assistant-select-row__add"
                          :title="t('insight.aiSettings.btnCreateModel')"
                          :aria-label="t('insight.aiSettings.btnCreateModel')"
                          @click="openAddModel"
                        >
                          <Plus :size="14" />
                        </ElButton>
                      </div>
                    </div>
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.multimodalModelHint') }}
                    </p>
                  </ElFormItem>
                </div>

                <ElFormItem
                  data-validation-field="maxConcurrency"
                  :error="errors.maxConcurrency"
                  :label="t('insight.assistants.fieldMaxConcurrency')"
                  required
                >
                  <ElInputNumber
                    v-model="maxConcurrency"
                    :min="1"
                    :max="50"
                    class="assistant-concurrency-input"
                    @change="clearFieldError('maxConcurrency')"
                  />
                  <p class="assistant-field-hint">
                    {{ t('insight.assistants.maxConcurrencyHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem
                  :label="t('insight.assistants.fieldAgentRounds')"
                  required
                >
                  <ElRadioGroup
                    v-model="agentRounds"
                    class="assistant-rounds-grid"
                  >
                    <ElRadio
                      v-for="tier in agentRoundsTiers"
                      :key="tier.value"
                      :value="tier.value"
                      border
                      class="assistant-rounds-card !mr-0"
                    >
                      <div class="assistant-rounds-card__inner">
                        <span class="assistant-rounds-card__label">{{ tier.label }}</span>
                        <span class="assistant-rounds-card__hint">{{ tier.hint }}</span>
                      </div>
                    </ElRadio>
                  </ElRadioGroup>
                  <p class="assistant-field-hint">
                    {{ t('insight.assistants.fieldAgentRoundsHint') }}
                  </p>
                </ElFormItem>
              </ElForm>
            </section>

            <!-- 2. Knowledge Source -->
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.assistants.sectionKnowledgeSource') }}
              </h3>
              <ElForm
                label-position="top"
                class="fullscreen-form-el-form"
              >
                <div class="fullscreen-form-grid">
                  <ElFormItem
                    data-validation-field="knowledgeSource"
                    :error="errors.knowledgeSource"
                    :label="t('insight.assistants.fieldKnowledgeSource')"
                    required
                  >
                    <div class="assistant-select-row">
                      <div class="assistant-select-row__controls">
                        <ElSelect
                          v-model="knowledgeSourceId"
                          filterable
                          class="assistant-select-row__select"
                          :loading="formOptionsRefreshing"
                          :placeholder="t('insight.assistants.phSelectKnowledgeSource')"
                          :disabled="selectableKnowledgeSources.length === 0 && !formOptionsRefreshing"
                          @change="clearFieldError('knowledgeSource')"
                        >
                          <ElOption
                            v-for="row in selectableKnowledgeSources"
                            :key="row.id"
                            :label="knowledgeSourceOptionLabel(row)"
                            :value="row.id"
                          >
                            <div class="assistant-ks-option">
                              <span class="assistant-ks-option__name">{{ row.name }}</span>
                              <span class="assistant-ks-option__meta">
                                {{
                                  t('insight.assistants.knowledgeSourceOptionMeta', {
                                    gateway: row.gateway_name,
                                    status: knowledgeSourceStatusLabel(row.status),
                                  })
                                }}
                              </span>
                            </div>
                          </ElOption>
                        </ElSelect>
                        <ElButton
                          class="hfl-refresh-button assistant-select-row__refresh"
                          :title="t('common.refresh')"
                          :aria-label="t('common.refresh')"
                          :disabled="formOptionsRefreshing"
                          @click="refreshFormOptions"
                        >
                          <RefreshCw
                            :size="16"
                            :class="{ 'is-spinning': formOptionsRefreshing }"
                          />
                        </ElButton>
                        <ElButton
                          class="fullscreen-form-icon-btn assistant-select-row__add"
                          :title="t('insight.kb.addPageTitle')"
                          :aria-label="t('insight.kb.addPageTitle')"
                          @click="openAddKnowledgeSource"
                        >
                          <Plus :size="14" />
                        </ElButton>
                      </div>
                    </div>
                    <p
                      v-if="selectableKnowledgeSources.length === 0"
                      class="assistant-field-hint assistant-field-hint--warn"
                    >
                      {{ t('insight.assistants.knowledgeSourceEmpty') }}
                    </p>
                    <p
                      v-else
                      class="assistant-field-hint"
                    >
                      {{ t('insight.assistants.fieldKnowledgeSourceHint') }}
                    </p>
                  </ElFormItem>

                  <ElFormItem
                    data-validation-field="scenario"
                    :error="errors.scenario"
                    :label="t('insight.assistants.fieldScenario')"
                    required
                  >
                    <ElSelect
                      v-model="selectedTask"
                      filterable
                      class="w-full"
                      :placeholder="t('insight.assistants.phSelectScenario')"
                      :disabled="!selectedKnowledgeSource"
                      @change="clearFieldError('scenario')"
                    >
                      <ElOption
                        v-for="task in taskOptions"
                        :key="task.name"
                        :label="task.title || task.name"
                        :value="task.name"
                      />
                    </ElSelect>
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.fieldScenarioHint') }}
                    </p>
                  </ElFormItem>
                </div>

                <div class="assistant-retrieval-grid">
                  <ElFormItem :label="t('insight.assistants.fieldRetrievalScope')">
                    <ElInput
                      v-model="retrievalScopeText"
                      type="textarea"
                      :rows="4"
                      class="font-mono assistant-retrieval-input"
                      :placeholder="t('insight.assistants.fieldRetrievalScopePlaceholder')"
                      :disabled="!selectedKnowledgeSource"
                    />
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.fieldRetrievalScopeHint') }}
                    </p>
                  </ElFormItem>
                  <ElFormItem :label="t('insight.assistants.fieldExcludeExtensions')">
                    <ElInput
                      v-model="excludeExtensionsText"
                      type="textarea"
                      :rows="4"
                      class="font-mono assistant-retrieval-input"
                      :placeholder="DEFAULT_EXCLUDE_EXTENSIONS"
                    />
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.fieldExcludeExtensionsHint') }}
                    </p>
                  </ElFormItem>
                  <ElFormItem :label="t('insight.assistants.fieldExcludeDirs')">
                    <ElInput
                      v-model="excludeDirsText"
                      type="textarea"
                      :rows="4"
                      class="font-mono assistant-retrieval-input"
                      :placeholder="DEFAULT_EXCLUDE_DIRS"
                    />
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.fieldExcludeDirsHint') }}
                    </p>
                  </ElFormItem>
                </div>
              </ElForm>
            </section>

            <!-- 3. Skills & Workspace -->
            <section class="fullscreen-form-card fullscreen-form-section assistant-section--tools">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.assistants.sectionTools') }}
              </h3>

              <ElForm
                label-position="top"
                class="fullscreen-form-el-form assistant-tools-form"
              >
                <ElFormItem :label="t('insight.assistants.fieldWorkspaceContext')">
                  <ElInput
                    v-model="workspaceGuideOverview"
                    type="textarea"
                    :rows="10"
                    class="assistant-workspace-input font-mono"
                    :placeholder="t('insight.assistants.workspaceContextPh')"
                  />
                  <p class="assistant-field-hint">
                    {{ t('insight.assistants.workspaceContextHint') }}
                  </p>
                </ElFormItem>

                <div class="assistant-tools-bindings">
                  <ElFormItem>
                    <template #label>
                      <span class="assistant-field-label-row">
                        <span>{{ t('insight.assistants.skillsSection') }}</span>
                        <span class="assistant-field-label-row__actions">
                          <ElButton
                            class="hfl-refresh-button assistant-field-label-row__refresh"
                            :title="t('common.refresh')"
                            :aria-label="t('common.refresh')"
                            :disabled="formOptionsRefreshing"
                            @click="refreshFormOptions"
                          >
                            <RefreshCw
                              :size="16"
                              :class="{ 'is-spinning': formOptionsRefreshing }"
                            />
                          </ElButton>
                          <ElButton
                            class="fullscreen-form-icon-btn assistant-field-label-row__add"
                            :title="t('insight.skills.btnCreate')"
                            :aria-label="t('insight.skills.btnCreate')"
                            @click="openAddSkill"
                          >
                            <Plus :size="14" />
                          </ElButton>
                        </span>
                      </span>
                    </template>
                    <div class="assistant-binding-panel">
                      <div
                        v-if="selectableSkills.length"
                        class="assistant-binding-panel__toolbar"
                      >
                        <span class="assistant-binding-panel__count">
                          {{
                            t('insight.assistants.bindingsSelectedCount', {
                              selected: selectedSkillCount,
                              total: selectableSkills.length,
                            })
                          }}
                        </span>
                      </div>
                      <ElCheckboxGroup
                        v-if="selectableSkills.length"
                        v-model="skillUuids"
                        class="assistant-binding-panel__list"
                      >
                        <label
                          v-for="skill in selectableSkills"
                          :key="skill.uuid"
                          class="assistant-binding-row"
                          :class="{ 'is-selected': isSkillSelected(skill.uuid) }"
                        >
                          <ElCheckbox :value="skill.uuid" />
                          <div class="assistant-binding-row__body">
                            <div class="assistant-binding-row__name">{{ skill.name }}</div>
                          </div>
                          <HflBooleanStatusTag
                            :value="skill.enabled"
                            :label="skill.enabled
                              ? t('insight.assistants.statusActive')
                              : t('insight.assistants.statusDisabled')"
                            effect="plain"
                          />
                        </label>
                      </ElCheckboxGroup>
                      <p
                        v-else
                        class="assistant-binding-panel__empty"
                      >
                        {{ t('insight.assistants.noSkills') }}
                      </p>
                    </div>
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.fieldSkillsHint') }}
                    </p>
                  </ElFormItem>

                  <ElFormItem>
                    <template #label>
                      <span class="assistant-field-label-row">
                        <span>{{ t('insight.assistants.mcpSection') }}</span>
                        <span class="assistant-field-label-row__actions">
                          <ElButton
                            class="hfl-refresh-button assistant-field-label-row__refresh"
                            :title="t('common.refresh')"
                            :aria-label="t('common.refresh')"
                            :disabled="formOptionsRefreshing"
                            @click="refreshFormOptions"
                          >
                            <RefreshCw
                              :size="16"
                              :class="{ 'is-spinning': formOptionsRefreshing }"
                            />
                          </ElButton>
                          <ElButton
                            class="fullscreen-form-icon-btn assistant-field-label-row__add"
                            :title="t('insight.mcpServers.btnCreate')"
                            :aria-label="t('insight.mcpServers.btnCreate')"
                            @click="openAddMcp"
                          >
                            <Plus :size="14" />
                          </ElButton>
                        </span>
                      </span>
                    </template>
                    <div class="assistant-binding-panel">
                      <div
                        v-if="mcps.length"
                        class="assistant-binding-panel__toolbar"
                      >
                        <span class="assistant-binding-panel__count">
                          {{
                            t('insight.assistants.bindingsSelectedCount', {
                              selected: selectedMcpCount,
                              total: mcps.length,
                            })
                          }}
                        </span>
                      </div>
                      <ElCheckboxGroup
                        v-if="mcps.length"
                        v-model="mcpUuids"
                        class="assistant-binding-panel__list"
                      >
                        <label
                          v-for="mcp in mcps"
                          :key="mcp.uuid"
                          class="assistant-binding-row"
                          :class="{ 'is-selected': isMcpSelected(mcp.uuid) }"
                        >
                          <ElCheckbox :value="mcp.uuid" />
                          <div class="assistant-binding-row__body">
                            <div class="assistant-binding-row__name">{{ mcp.name }}</div>
                            <div class="assistant-binding-row__meta">
                              {{ mcp.transport }} · {{ mcp.endpoint || '—' }}
                            </div>
                          </div>
                          <HflBooleanStatusTag
                            :value="mcp.enabled"
                            :label="mcp.enabled
                              ? t('insight.assistants.statusActive')
                              : t('insight.assistants.statusDisabled')"
                            effect="plain"
                          />
                        </label>
                      </ElCheckboxGroup>
                      <p
                        v-else
                        class="assistant-binding-panel__empty"
                      >
                        {{ t('insight.assistants.noMcp') }}
                      </p>
                    </div>
                    <p class="assistant-field-hint">
                      {{ t('insight.assistants.fieldMcpHint') }}
                    </p>
                  </ElFormItem>
                </div>
              </ElForm>
            </section>

            <!-- 4. Visibility -->
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.assistants.sectionVisibility') }}
              </h3>
              <ElRadioGroup
                v-model="visibilityScope"
                class="assistant-visibility-grid"
              >
                <ElRadio
                  value="user"
                  border
                  class="assistant-visibility-card !mr-0"
                >
                  <div class="assistant-visibility-card__inner">
                    <div class="assistant-visibility-card__title">
                      {{ t('insight.assistants.visibilityOnlyMe') }}
                    </div>
                    <div class="assistant-visibility-card__desc">
                      {{ t('insight.assistants.visibilityOnlyMeDesc') }}
                    </div>
                  </div>
                </ElRadio>
                <ElRadio
                  value="organization"
                  border
                  class="assistant-visibility-card !mr-0"
                >
                  <div class="assistant-visibility-card__inner">
                    <div class="assistant-visibility-card__title">
                      {{ t('insight.assistants.visibilityOrganization') }}
                    </div>
                    <div class="assistant-visibility-card__desc">
                      {{ t('insight.assistants.visibilityOrganizationDesc') }}
                    </div>
                  </div>
                </ElRadio>
              </ElRadioGroup>
            </section>
          </div>
        </div>
      </div>

      <footer class="fullscreen-form-footer">
        <ElButton @click="handleBack">
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :loading="saving"
          :disabled="saving || loading"
          @click="handleSubmit"
        >
          {{ isEditing ? t('common.save') : t('insight.assistants.btnCreate') }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.assistant-form-fullscreen .fullscreen-form-page {
  min-height: calc(var(--app-viewport-height) - var(--app-header-height));
}

.assistant-form-fullscreen .fullscreen-form-card,
.assistant-form-fullscreen .fullscreen-form-step-stack {
  overflow: visible;
}

.assistant-form-fullscreen :deep(.el-form-item__label) {
  color: var(--color-text-title);
  font-weight: 600;
}

.assistant-tools-bindings :deep(.el-form-item__label) {
  display: flex;
  width: 100%;
  padding-right: 0;
}

.assistant-form-fullscreen :deep(.el-form-item) {
  margin-bottom: 18px;
}

.assistant-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.assistant-field-hint--warn {
  color: var(--color-warning, #e6a23c);
}

.assistant-select-row {
  width: 100%;
}

.assistant-select-row__controls {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.assistant-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.assistant-select-row__refresh {
  flex: 0 0 34px;
}

.assistant-select-row__refresh .is-spinning {
  animation: assistant-select-spin 1s linear infinite;
}

.assistant-select-row__add {
  flex: 0 0 40px;
}

.assistant-field-label-row {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding-right: 2px;
}

.assistant-field-label-row__actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.assistant-field-label-row__refresh {
  flex: 0 0 34px;
}

.assistant-field-label-row__refresh .is-spinning {
  animation: assistant-select-spin 1s linear infinite;
}

.assistant-field-label-row__add {
  flex: 0 0 40px;
}

@keyframes assistant-select-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 900px) {
  .assistant-select-row__controls {
    flex-wrap: wrap;
  }

  .assistant-select-row__select {
    flex-basis: 100%;
  }
}

.assistant-ks-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.assistant-ks-option__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title);
}

.assistant-ks-option__meta {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.assistant-retrieval-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  width: 100%;
}

@media (max-width: 960px) {
  .assistant-retrieval-grid {
    grid-template-columns: 1fr;
  }
}

.assistant-retrieval-grid :deep(.el-form-item) {
  margin-bottom: 0;
}

.assistant-retrieval-input :deep(textarea) {
  min-height: 108px;
  font-size: 13px;
  line-height: 1.55;
}

.assistant-concurrency-input {
  width: 140px;
}

.assistant-rounds-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}

@media (max-width: 900px) {
  .assistant-rounds-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.assistant-rounds-grid :deep(.el-radio__input),
.assistant-visibility-grid :deep(.el-radio__input) {
  display: none;
}

.assistant-rounds-grid :deep(.el-radio__label),
.assistant-visibility-grid :deep(.el-radio__label) {
  padding-left: 0;
  width: 100%;
}

.assistant-rounds-grid :deep(.el-radio) {
  height: auto !important;
  min-height: 64px;
  margin-right: 0 !important;
  padding: 12px 10px !important;
  border-radius: 12px !important;
  align-items: center;
  justify-content: center;
}

.assistant-rounds-grid :deep(.el-radio.is-bordered.is-checked) {
  border-color: var(--color-primary, #6d5ef6) !important;
  background: var(--color-primary-light, #f2f0fe);
}

.assistant-rounds-card__inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  width: 100%;
  text-align: center;
}

.assistant-rounds-card__label {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title);
}

.assistant-rounds-card__hint {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.assistant-tools-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.assistant-tools-bindings {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 900px) {
  .assistant-tools-bindings {
    grid-template-columns: 1fr;
  }
}

.assistant-tools-bindings :deep(.el-form-item) {
  margin-bottom: 0;
}

.assistant-workspace-input :deep(textarea) {
  min-height: 240px;
  font-size: 13px;
  line-height: 1.55;
}

.assistant-binding-panel {
  width: 100%;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-card, 12px);
  background: var(--color-grey-2, #f8fafc);
  overflow: hidden;
}

.assistant-binding-panel__toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border-light);
  background: color-mix(in srgb, var(--color-grey-2, #f8fafc) 70%, white);
}

.assistant-binding-panel__count {
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-border-light);
  background: var(--color-card-bg, #fff);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.assistant-binding-panel__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 280px;
  overflow: auto;
  padding: 8px;
}

.assistant-binding-panel__empty {
  margin: 0;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-secondary);
}

.assistant-binding-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.assistant-binding-row:hover {
  background: var(--color-card-bg, #fff);
}

.assistant-binding-row.is-selected {
  border-color: color-mix(in srgb, var(--color-primary, #6d5ef6) 35%, transparent);
  background: var(--color-primary-light, #f2f0fe);
}

.assistant-binding-row__body {
  flex: 1;
  min-width: 0;
}

.assistant-binding-row__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-title);
}

.assistant-binding-row__meta {
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-visibility-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}

@media (max-width: 640px) {
  .assistant-visibility-grid {
    grid-template-columns: 1fr;
  }
}

.assistant-visibility-grid :deep(.el-radio) {
  height: auto !important;
  min-height: 88px;
  margin-right: 0 !important;
  padding: 14px 16px !important;
  border-radius: 12px !important;
  align-items: flex-start;
}

.assistant-visibility-grid :deep(.el-radio.is-bordered.is-checked) {
  border-color: var(--color-primary, #6d5ef6) !important;
  background: var(--color-primary-light, #f2f0fe);
}

.assistant-visibility-card__inner {
  width: 100%;
}

.assistant-visibility-card__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title);
}

.assistant-visibility-card__desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}
</style>
