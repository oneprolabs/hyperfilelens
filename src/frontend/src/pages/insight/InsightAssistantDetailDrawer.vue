<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
import { lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflDetailDrawerFooter from '../../components/HflDetailDrawerFooter.vue'
import {
  fetchLensAssistant,
  fetchLensAssistantFormOptions,
  listLensModels,
  type LensAssistant,
  type LensAssistantFormOptions,
  type LensLlmConfig,
} from '../../lib/lensApi'

const props = defineProps<{
  open: boolean
  row: LensAssistant | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  edit: [uuid: string]
}>()

const { t } = useI18n()
const loading = ref(false)
const detailRaw = ref<Record<string, unknown> | null>(null)
const models = ref<LensLlmConfig[]>([])
const formOptions = ref<LensAssistantFormOptions | null>(null)
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const modelByUuid = computed(() => new Map(models.value.map((row) => [row.uuid, row])))

const activeRow = computed(() => detailRaw.value ?? (props.row as Record<string, unknown> | null))

const drawerTitle = computed(() => {
  const name = activeRow.value?.name
  return typeof name === 'string' && name.trim() ? name.trim() : props.row?.name || DETAIL_EMPTY
})

const knowledgeSources = computed(() => formOptions.value?.knowledge_sources ?? [])
const gateways = computed(() => formOptions.value?.gateways ?? [])
const skills = computed(() => formOptions.value?.skills ?? [])
const mcps = computed(() => formOptions.value?.mcps ?? [])

const knowledgeSourceId = computed(() => {
  const raw = activeRow.value?.knowledge_source_id ?? props.row?.knowledge_source_id
  if (raw == null || raw === '') return null
  return Number(raw)
})

const selectedKnowledgeSource = computed(
  () => knowledgeSources.value.find((row) => row.id === knowledgeSourceId.value) ?? null,
)

const selectedGateway = computed(() => {
  const lensnodeUuid =
    selectedKnowledgeSource.value?.lensnode_uuid ||
    String(activeRow.value?.lensnode_uuid || activeRow.value?.lensnode || '')
  return gateways.value.find((row) => row.lensnode_uuid === lensnodeUuid) ?? null
})

const agentModelLabel = computed(() => modelRefLabel(activeRow.value?.agent_model_ref))
const multimodalModelLabel = computed(() => modelRefLabel(activeRow.value?.multimodal_model_ref))

const scenarioLabel = computed(() => {
  const taskName = String(activeRow.value?.selected_task || '')
  if (!taskName) return DETAIL_EMPTY
  const task = selectedGateway.value?.tasks.find((item) => item.name === taskName)
  return task?.title || taskName
})

const knowledgeSourceLabel = computed(() => {
  if (selectedKnowledgeSource.value?.name) return selectedKnowledgeSource.value.name
  if (props.row?.knowledge_source_name) return props.row.knowledge_source_name
  return DETAIL_EMPTY
})

const gatewayLabel = computed(() => {
  if (selectedKnowledgeSource.value?.gateway_name) return selectedKnowledgeSource.value.gateway_name
  if (props.row?.gateway_name) return props.row.gateway_name
  return DETAIL_EMPTY
})

const knowledgeSourceStatusRaw = computed(() =>
  String(
    selectedKnowledgeSource.value?.status ||
      props.row?.knowledge_source_status ||
      '',
  ).toLowerCase(),
)

const knowledgeSourceStatusDisplay = computed(() => {
  if (!knowledgeSourceStatusRaw.value) return DETAIL_EMPTY
  return knowledgeSourceStatusLabel(knowledgeSourceStatusRaw.value)
})

const knowledgeSourceStatusTagAttrs = computed(() => lifecycleStatusTagAttrs(knowledgeSourceStatusRaw.value))

const retrievalScopeLines = computed(() => {
  const dirs = activeRow.value?.selected_dirs as
    | { retrieval_scope?: { include_paths?: string[] } }[]
    | undefined
  const paths = new Set<string>()
  for (const dir of dirs ?? []) {
    for (const path of dir.retrieval_scope?.include_paths ?? []) {
      const normalized = String(path || '').trim()
      if (normalized) paths.add(normalized)
    }
  }
  return [...paths]
})

const excludeExtensionsText = computed(() => {
  const settings = (activeRow.value?.settings || {}) as {
    retrieval_policy?: { exclude_extensions?: string[] }
  }
  const items = settings.retrieval_policy?.exclude_extensions
  return Array.isArray(items) && items.length ? items.join(', ') : DETAIL_EMPTY
})

const excludeDirsText = computed(() => {
  const settings = (activeRow.value?.settings || {}) as {
    retrieval_policy?: { exclude_dirs?: string[] }
  }
  const items = settings.retrieval_policy?.exclude_dirs
  return Array.isArray(items) && items.length ? items.join(', ') : DETAIL_EMPTY
})

const workspaceContextText = computed(() => {
  const guide = activeRow.value?.workspace_guide as { content?: string } | undefined
  const text = String(guide?.content || '').trim()
  return text || DETAIL_EMPTY
})

const boundSkillNames = computed(() => {
  const wgSlugs = new Set(
    skills.value
      .filter((skill) => typeof skill.slug === 'string' && skill.slug.endsWith('-workspace-guide'))
      .map((skill) => skill.uuid),
  )
  const bindings = (activeRow.value?.skill_bindings || []) as {
    skill?: { uuid?: string; name?: string }
    skill_uuid?: string
  }[]
  return bindings
    .map((binding) => {
      const uuid = binding.skill?.uuid || binding.skill_uuid
      if (!uuid || wgSlugs.has(uuid)) return null
      const named = skills.value.find((row) => row.uuid === uuid)
      return named?.name || binding.skill?.name || uuid
    })
    .filter((name): name is string => Boolean(name))
})

const boundMcpNames = computed(() => {
  const bindings = (activeRow.value?.mcp_bindings || []) as {
    mcp_server?: { uuid?: string; name?: string }
    mcp_uuid?: string
  }[]
  return bindings
    .map((binding) => {
      const uuid = binding.mcp_server?.uuid || binding.mcp_uuid
      if (!uuid) return null
      const named = mcps.value.find((row) => row.uuid === uuid)
      return named?.name || binding.mcp_server?.name || uuid
    })
    .filter((name): name is string => Boolean(name))
})

const visibilityScope = computed(() => {
  const scope = activeRow.value?.visibility_scope ?? props.row?.visibility_scope
  return scope === 'organization' ? 'organization' : 'user'
})

const statusLabel = computed(() => {
  const status = String(activeRow.value?.status || props.row?.status || '')
  if (status === 'active') return t('insight.assistants.statusActive')
  if (status === 'disabled') return t('insight.assistants.statusDisabled')
  return status || DETAIL_EMPTY
})

const analysisDepthLabel = computed(() => {
  const value = String(activeRow.value?.agent_rounds || 'balanced')
  if (value === 'flash') return t('insight.assistants.roundsFlash')
  if (value === 'fast') return t('insight.assistants.roundsFast')
  if (value === 'balanced') return t('insight.assistants.roundsBalanced')
  if (value === 'deep') return t('insight.assistants.roundsDeep')
  if (value === 'max') return t('insight.assistants.roundsMax')
  return value
})

function modelRefLabel(ref: unknown) {
  if (!ref) return DETAIL_EMPTY
  const refStr = String(ref)
  const model = modelByUuid.value.get(refStr)
  if (!model) return refStr
  const provider = model.provider || model.name || 'provider'
  const modelId = model.config?.model || '—'
  return `${provider} · ${modelId}`
}

function knowledgeSourceStatusLabel(status: string) {
  if (status === 'ready') return t('insight.kb.statusReady')
  if (status === 'degraded') return t('insight.kb.statusDegraded')
  if (status === 'syncing') return t('insight.kb.statusSyncing')
  if (status === 'error') return t('insight.kb.statusError')
  if (status === 'paused') return t('insight.kb.statusPaused')
  return status
}

function detailValueClass(text: string | null | undefined, mono = false) {
  const value = text == null || text === '' ? DETAIL_EMPTY : String(text)
  const empty = value === DETAIL_EMPTY
  return {
    'hfl-detail-row__empty': empty,
    'hfl-detail-row__value--mono': mono && !empty,
  }
}

function displayValue(text: string | number | null | undefined) {
  if (text == null || text === '') return DETAIL_EMPTY
  return String(text)
}

async function loadDetail() {
  if (!props.row?.uuid) {
    detailRaw.value = null
    return
  }
  loading.value = true
  try {
    const [row, modelRows, options] = await Promise.all([
      fetchLensAssistant(props.row.uuid),
      listLensModels().catch(() => [] as LensLlmConfig[]),
      fetchLensAssistantFormOptions().catch(() => null),
    ])
    detailRaw.value = row
    models.value = modelRows
    formOptions.value = options
  } catch (err) {
    detailRaw.value = props.row as unknown as Record<string, unknown>
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    loading.value = false
  }
}

function onEdit() {
  const uuid = props.row?.uuid
  if (!uuid) return
  drawerOpen.value = false
  emit('edit', uuid)
}

function onDrawerOpened() {
  bindDrawerResize()
}

function onDrawerClosed() {
  unbindDrawerResize()
  detailRaw.value = null
  models.value = []
  formOptions.value = null
}

watch(
  () => [props.open, props.row?.uuid] as const,
  ([isOpen]) => {
    if (isOpen) void loadDetail()
  },
)

watch(drawerOpen, (isOpen) => {
  if (isOpen) {
    void nextTick(() => requestAnimationFrame(() => updateDrawerWidth()))
  }
})

onUnmounted(() => {
  unbindDrawerResize()
})
</script>

<template>
  <ElDrawer
    v-model="drawerOpen"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer insight-detail-drawer"
    @opened="onDrawerOpened"
    @closed="onDrawerClosed"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ drawerTitle }}</span>
    </template>

    <div
      v-loading="loading"
      class="hfl-detail-drawer__body"
    >
      <template v-if="activeRow">
        <div class="hfl-detail-sections">
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.assistants.sectionBasics') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(displayValue(activeRow.name))"
                >
                  {{ displayValue(activeRow.name) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.colStatus') }}</span>
                <span class="hfl-detail-row__value">
                  <ElTag
                    v-bind="lifecycleStatusTagAttrs(String(activeRow.status || ''))"
                    size="small"
                  >
                    {{ statusLabel }}
                  </ElTag>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldAgentModel') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(agentModelLabel)"
                >
                  {{ agentModelLabel }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldMultimodalModel') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(multimodalModelLabel)"
                >
                  {{ multimodalModelLabel }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldMaxConcurrency') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(displayValue(activeRow.max_concurrency))"
                >
                  {{ displayValue(activeRow.max_concurrency) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldAgentRounds') }}</span>
                <span class="hfl-detail-row__value">
                  <ElTag
                    size="small"
                    effect="plain"
                  >{{ analysisDepthLabel }}</ElTag>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.assistants.sectionKnowledgeSource') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldKnowledgeSource') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(knowledgeSourceLabel)"
                >
                  {{ knowledgeSourceLabel }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldGateway') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(gatewayLabel)"
                >
                  {{ gatewayLabel }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.kb.colLearnStatus') }}</span>
                <span class="hfl-detail-row__value">
                  <ElTag
                    v-if="knowledgeSourceStatusDisplay !== DETAIL_EMPTY"
                    size="small"
                    v-bind="knowledgeSourceStatusTagAttrs"
                  >
                    {{ knowledgeSourceStatusDisplay }}
                  </ElTag>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldScenario') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(scenarioLabel)"
                >
                  {{ scenarioLabel }}
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldRetrievalScope') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <div
                    v-if="retrievalScopeLines.length"
                    class="create-filter-rules-preview"
                  >
                    <code
                      v-for="line in retrievalScopeLines"
                      :key="line"
                      class="create-filter-rules-preview__line"
                    >{{ line }}</code>
                  </div>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldExcludeExtensions') }}</span>
                <span
                  class="hfl-detail-row__value hfl-detail-row__value--mono"
                  :class="detailValueClass(excludeExtensionsText === DETAIL_EMPTY ? '' : excludeExtensionsText, true)"
                >
                  {{ excludeExtensionsText }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldExcludeDirs') }}</span>
                <span
                  class="hfl-detail-row__value hfl-detail-row__value--mono"
                  :class="detailValueClass(excludeDirsText === DETAIL_EMPTY ? '' : excludeDirsText, true)"
                >
                  {{ excludeDirsText }}
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.assistants.sectionTools') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldWorkspaceContext') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <div
                    v-if="workspaceContextText !== DETAIL_EMPTY"
                    class="create-filter-rules-preview"
                  >
                    <pre class="create-filter-rules-preview__line">{{ workspaceContextText }}</pre>
                  </div>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.skillsSection') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <span
                    v-if="boundSkillNames.length"
                    class="insight-detail-tag-row"
                  >
                    <ElTag
                      v-for="name in boundSkillNames"
                      :key="name"
                      size="small"
                      effect="plain"
                    >
                      {{ name }}
                    </ElTag>
                  </span>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.mcpSection') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <span
                    v-if="boundMcpNames.length"
                    class="insight-detail-tag-row"
                  >
                    <ElTag
                      v-for="name in boundMcpNames"
                      :key="name"
                      size="small"
                      effect="plain"
                    >
                      {{ name }}
                    </ElTag>
                  </span>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.assistants.sectionVisibility') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.assistants.fieldVisibility') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <ElTag
                    :type="visibilityScope === 'user' ? 'warning' : 'success'"
                    size="small"
                  >
                    {{
                      visibilityScope === 'user'
                        ? t('insight.assistants.visibilityOnlyMe')
                        : t('insight.assistants.visibilityOrganization')
                    }}
                  </ElTag>
                  <span class="insight-detail-row-hint">
                    {{
                      visibilityScope === 'user'
                        ? t('insight.assistants.visibilityOnlyMeDesc')
                        : t('insight.assistants.visibilityOrganizationDesc')
                    }}
                  </span>
                </span>
              </div>
            </div>
          </section>
        </div>
      </template>
    </div>

    <template
      v-if="activeRow"
      #footer
    >
      <HflDetailDrawerFooter
        :save-label="t('common.edit')"
        @cancel="drawerOpen = false"
        @save="onEdit"
      />
    </template>
  </ElDrawer>
</template>
