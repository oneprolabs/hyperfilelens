<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import AiProviderIcon from '../../components/ai-model/AiProviderIcon.vue'
import { aiProviderLabel } from '../../lib/aiProviderDisplay'
import { defaultAiModelDisplayName } from '../../lib/aiModelDisplay'
import { copyTextToClipboard } from '../../lib/clipboard'
import { capabilityClass, lookupModelCapabilities } from '../../lib/aiModelCapabilities'
import { DETAIL_EMPTY, isDetailEmpty } from '../../lib/nodeInventoryDisplay'
import {
  fetchLensModelCatalog,
  fetchLensModelDetail,
  testSavedLensModel,
  type LensLlmConfig,
} from '../../lib/lensApi'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'

const props = defineProps<{
  modelValue: boolean
  modelUuid: string | null
  refreshToken?: number
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  edit: [uuid: string]
}>()

const { t } = useI18n()

const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const detail = ref<LensLlmConfig | null>(null)
const busy = ref(false)
const testing = ref(false)
const modelCapabilities = ref<string[]>([])
const capabilityLabels = ref<Record<string, string>>({})
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const title = computed(() => displayName.value)

const displayName = computed(() => {
  if (!detail.value) return DETAIL_EMPTY
  if (detail.value.name?.trim()) return detail.value.name.trim()
  return defaultAiModelDisplayName(
    detail.value.provider || 'provider',
    detail.value.config?.model || DETAIL_EMPTY,
    aiProviderLabel(detail.value.provider || '', detail.value.provider || DETAIL_EMPTY),
    detail.value.config?.model,
  )
})

const providerLabel = computed(() =>
  aiProviderLabel(detail.value?.provider || detail.value?.name || '', detail.value?.provider || DETAIL_EMPTY),
)

const apiKeyDisplay = computed(() => {
  const key = detail.value?.config?.api_key?.trim()
  if (!key) return DETAIL_EMPTY
  if (/^[*•·]+$/.test(key)) return key
  return '*'.repeat(Math.min(Math.max(key.length, 8), 24))
})

const hasApiKey = computed(() => apiKeyDisplay.value !== DETAIL_EMPTY)

const modelVersion = computed(() => detail.value?.config?.model?.trim() || DETAIL_EMPTY)
const apiBase = computed(() => detail.value?.config?.api_base?.trim() || DETAIL_EMPTY)
const displayModelUuid = computed(() => detail.value?.uuid || DETAIL_EMPTY)

function capabilityLabel(key: string) {
  return capabilityLabels.value[key] || key
}

let refreshSeq = 0

async function refresh() {
  const uuid = props.modelUuid
  if (!uuid) {
    detail.value = null
    modelCapabilities.value = []
    capabilityLabels.value = {}
    return
  }
  const seq = ++refreshSeq
  busy.value = true
  try {
    const [row, catalogRaw] = await Promise.all([
      fetchLensModelDetail(uuid),
      fetchLensModelCatalog().catch(() => null),
    ])
    if (seq !== refreshSeq) return
    detail.value = row
    const lookup = lookupModelCapabilities(catalogRaw, row.provider || '', row.config?.model || '')
    modelCapabilities.value = lookup.capabilities
    capabilityLabels.value = lookup.labels
  } catch {
    if (seq === refreshSeq) {
      detail.value = null
      modelCapabilities.value = []
      capabilityLabels.value = {}
    }
  } finally {
    if (seq === refreshSeq) busy.value = false
  }
}

function onDrawerOpened() {
  bindDrawerResize()
}

function onDrawerClosed() {
  refreshSeq += 1
  unbindDrawerResize()
  detail.value = null
  modelCapabilities.value = []
  capabilityLabels.value = {}
}

function onEdit() {
  const uuid = detail.value?.uuid || props.modelUuid
  if (!uuid) return
  open.value = false
  emit('edit', uuid)
}

async function testConnection() {
  const uuid = detail.value?.uuid || props.modelUuid
  if (!uuid) return
  testing.value = true
  try {
    const response = await testSavedLensModel(uuid)
    const result = response as { ok?: boolean; success?: boolean }
    if (result.ok === false || result.success === false) {
      throw new Error(t('insight.aiSettings.connectivityFail', { detail: '' }))
    }
    ElMessage.success({ message: t('insight.aiSettings.connectivityOk'), grouping: true })
  } catch {
    ElMessage.error({ message: t('insight.aiSettings.connectivityFail', { detail: '' }), grouping: true })
  } finally {
    testing.value = false
  }
}

function detailValueClass(text: string, mono = false) {
  const empty = !text || text === DETAIL_EMPTY
  return {
    'hfl-detail-row__empty': empty,
    'hfl-detail-row__value--mono': mono && !empty,
  }
}

async function copyText(value: string) {
  if (isDetailEmpty(value)) return
  try {
    await copyTextToClipboard(value)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('protection.sourceResources.copyFailed'), grouping: true })
  }
}

watch(
  () => [open.value, props.modelUuid, props.refreshToken] as const,
  ([isOpen, uuid]) => {
    if (isOpen && uuid) void refresh()
    if (!isOpen) {
      refreshSeq += 1
      detail.value = null
      modelCapabilities.value = []
      capabilityLabels.value = {}
    }
  },
)

watch(open, (isOpen) => {
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
    v-model="open"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer"
    @opened="onDrawerOpened"
    @closed="onDrawerClosed"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ title }}</span>
    </template>

    <div
      v-loading="busy"
      class="hfl-detail-drawer__body"
    >
      <template v-if="detail">
        <div class="hfl-detail-sections">
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.aiSettings.sectionCredentials') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.colName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(displayName)"
                >
                  {{ displayName }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelUuid') }}</span>
                <span
                  class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono ai-model-drawer__uuid"
                  :class="detailValueClass(displayModelUuid, true)"
                >
                  <span class="hfl-detail-row__text">{{ displayModelUuid }}</span>
                  <ElButton
                    v-if="!isDetailEmpty(displayModelUuid)"
                    text
                    circle
                    size="small"
                    class="hfl-detail-row__edit"
                    :title="t('common.copy')"
                    @click="copyText(displayModelUuid)"
                  >
                    <Copy :size="13" />
                  </ElButton>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelProvider') }}</span>
                <span class="hfl-detail-row__value">
                  <span class="ai-model-drawer__provider">
                    <AiProviderIcon
                      v-if="detail.provider || detail.name"
                      :provider="detail.provider || detail.name || ''"
                      size="sm"
                    />
                    <span>{{ providerLabel }}</span>
                  </span>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelActive') }}</span>
                <span class="hfl-detail-row__value">
                  <HflBooleanStatusTag
                    :value="detail.is_active !== false"
                    :label="detail.is_active !== false
                      ? t('insight.aiSettings.statusActive')
                      : t('insight.aiSettings.statusInactive')"
                  />
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelDefaultAgent') }}</span>
                <span class="hfl-detail-row__value">
                  <HflBooleanStatusTag
                    :value="detail.is_default_agent === true"
                    :label="detail.is_default_agent ? t('common.yes') : t('common.no')"
                  />
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelDefaultMultimodal') }}</span>
                <span class="hfl-detail-row__value">
                  <HflBooleanStatusTag
                    :value="detail.is_default_multimodal === true"
                    :label="detail.is_default_multimodal ? t('common.yes') : t('common.no')"
                  />
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelManagedBy') }}</span>
                <span class="hfl-detail-row__value">
                  {{ detail.deployment_managed ? t('insight.aiSettings.deploymentManagedBadge') : t('insight.aiSettings.manuallyManagedBadge') }}
                </span>
              </div>

              <div
                class="hfl-detail-row ai-model-drawer__divider"
                aria-hidden="true"
              />

              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelModel') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(modelVersion, true)"
                >
                  {{ modelVersion }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelCapabilities') }}</span>
                <span class="hfl-detail-row__value">
                  <span
                    v-if="modelCapabilities.length"
                    class="ai-model-drawer__caps"
                  >
                    <span
                      v-for="cap in modelCapabilities"
                      :key="cap"
                      class="ai-cap-tag"
                      :class="capabilityClass(cap)"
                    >
                      {{ capabilityLabel(cap) }}
                    </span>
                  </span>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelApiBase') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(apiBase, true)"
                >
                  {{ apiBase }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.aiSettings.labelApiKey') }}</span>
                <span
                  class="hfl-detail-row__value hfl-detail-row__value--mono ai-model-drawer__api-key"
                  :class="{ 'hfl-detail-row__empty': !hasApiKey }"
                >
                  {{ apiKeyDisplay }}
                </span>
              </div>
            </div>
          </section>
        </div>
      </template>

      <ElEmpty
        v-else-if="!busy"
        :description="t('errors.generic.loadFailed')"
        :image-size="72"
      />
    </div>

    <template
      v-if="detail"
      #footer
    >
      <div class="el-drawer__footer-actions">
        <ElButton
          :disabled="testing"
          @click="open = false"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          :loading="testing"
          @click="testConnection"
        >
          {{ t('insight.aiSettings.testConnection') }}
        </ElButton>
        <ElButton
          v-if="!detail.deployment_managed"
          type="primary"
          :disabled="testing"
          @click="onEdit"
        >
          {{ t('common.edit') }}
        </ElButton>
      </div>
    </template>
  </ElDrawer>
</template>

<style scoped>
.ai-model-drawer__provider {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.ai-model-drawer__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.ai-cap-tag {
  display: inline-flex;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 600;
}

.cap-sky { background: #e0f2fe; color: #0369a1; }
.cap-emerald { background: #d1fae5; color: #047857; }
.cap-violet { background: #ede9fe; color: #6d28d9; }
.cap-indigo { background: #e0e7ff; color: #4338ca; }
.cap-rose { background: #ffe4e6; color: #be123c; }
.cap-teal { background: #ccfbf1; color: #0f766e; }
.cap-gray { background: #f1f5f9; color: #475569; }

.ai-model-drawer__divider {
  grid-column: 1 / -1;
  height: 1px;
  margin: 4px 0;
  padding: 0;
  background: var(--color-border-light, #e5e7eb);
  min-height: 1px;
}

.ai-model-drawer__uuid {
  font-size: 12px;
}

.ai-model-drawer__api-key {
  letter-spacing: 0.08em;
}
</style>
