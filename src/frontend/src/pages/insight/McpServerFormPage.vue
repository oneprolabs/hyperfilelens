<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensMcpPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Plus, Trash2 } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import { routeLocationWithListRefresh } from '../../lib/listRouteRefresh'
import {
  createLensMcpServer,
  fetchLensMcpServer,
  updateLensMcpServer,
  type LensMcpServer,
} from '../../lib/lensApi'

type ConfigRow = { key: string; value: string }

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const editingUuid = computed(() => {
  const raw = route.params.uuid
  return typeof raw === 'string' && raw ? raw : null
})

const isEditing = computed(() => Boolean(editingUuid.value))

const loading = ref(false)
const saving = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)

const name = ref('')
const transport = ref<'url' | 'stdio'>('url')
const endpoint = ref('')
const configRows = ref<ConfigRow[]>([])
const enabled = ref(true)

const pageTitle = computed(() => {
  if (isEditing.value && name.value.trim()) return name.value.trim()
  return t('insight.mcpServers.addPageTitle')
})

const pageDesc = computed(() => t('insight.mcpServers.addPageDesc'))

const endpointPlaceholder = computed(() =>
  transport.value === 'stdio'
    ? t('insight.mcpServers.fieldEndpointPhStdio')
    : t('insight.mcpServers.fieldEndpointPhUrl'),
)

function objectToRows(config: Record<string, unknown> | undefined): ConfigRow[] {
  if (!config || typeof config !== 'object') return []
  return Object.entries(config).map(([key, value]) => ({
    key,
    value: value == null ? '' : String(value),
  }))
}

function rowsToObject(rows: ConfigRow[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const row of rows) {
    const key = row.key.trim()
    if (!key) continue
    out[key] = row.value
  }
  return out
}

function applyRow(row: LensMcpServer) {
  name.value = row.name || ''
  transport.value = row.transport === 'stdio' ? 'stdio' : 'url'
  endpoint.value = row.endpoint || ''
  configRows.value = objectToRows(row.config)
  enabled.value = row.enabled !== false
}

function resetForm() {
  name.value = ''
  transport.value = 'url'
  endpoint.value = ''
  configRows.value = []
  enabled.value = true
}

function addConfigRow() {
  configRows.value.push({ key: '', value: '' })
}

function removeConfigRow(index: number) {
  configRows.value.splice(index, 1)
}

function buildPayload() {
  return {
    name: name.value.trim(),
    transport: transport.value,
    endpoint: endpoint.value.trim(),
    config: rowsToObject(configRows.value),
    enabled: enabled.value,
  }
}

async function loadDetail() {
  if (!editingUuid.value) return
  const row = await fetchLensMcpServer(editingUuid.value)
  applyRow(row)
}

async function init() {
  loading.value = true
  try {
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
  router.push(routeLocationWithListRefresh(lensMcpPath()))
}

async function handleSubmit() {
  if (saving.value) return
  if (!validateInline([
    { field: 'name', message: t('insight.mcpServers.fieldName'), valid: !!name.value.trim() },
    { field: 'endpoint', message: t('insight.mcpServers.fieldEndpoint'), valid: !!endpoint.value.trim() },
  ])) return
  saving.value = true
  try {
    const payload = buildPayload()
    if (isEditing.value && editingUuid.value) {
      await updateLensMcpServer(editingUuid.value, payload)
      ElMessage.success(t('insight.mcpServers.saveSuccess'))
    } else {
      await createLensMcpServer(payload)
      ElMessage.success(t('insight.mcpServers.createSuccess'))
    }
    router.push(routeLocationWithListRefresh(lensMcpPath()))
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('insight.mcpServers.saveFailed')))
  } finally {
    saving.value = false
  }
}

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
    class="fullscreen-form-fullscreen resource-add-fullscreen mcp-form-fullscreen"
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
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.mcpServers.sectionBasics') }}
              </h3>
              <p class="mcp-section-desc">
                {{ t('insight.mcpServers.sectionBasicsDesc') }}
              </p>

              <ElForm
                label-position="top"
                class="fullscreen-form-el-form fullscreen-form-el-form--strong-label"
              >
                <ElFormItem
                  data-validation-field="name"
                  class="fullscreen-form-item--in-card"
                  :error="errors.name"
                  :label="t('insight.mcpServers.fieldName')"
                  required
                >
                  <ElInput
                    v-model="name"
                    :placeholder="t('insight.mcpServers.fieldNamePh')"
                    @input="clearFieldError('name')"
                  />
                  <p class="mcp-field-hint">
                    {{ t('insight.mcpServers.fieldNameHint') }}
                  </p>
                </ElFormItem>

                <div class="fullscreen-form-grid mcp-connection-grid">
                  <ElFormItem
                    class="fullscreen-form-item--in-card"
                    :label="t('insight.mcpServers.fieldTransport')"
                    required
                  >
                    <ElSelect
                      v-model="transport"
                      class="w-full"
                    >
                      <ElOption
                        label="URL"
                        value="url"
                      />
                      <ElOption
                        label="STDIO"
                        value="stdio"
                      />
                    </ElSelect>
                    <p class="mcp-field-hint">
                      {{ t('insight.mcpServers.fieldTransportHint') }}
                    </p>
                  </ElFormItem>
                  <ElFormItem
                    data-validation-field="endpoint"
                    class="fullscreen-form-item--in-card"
                    :error="errors.endpoint"
                    :label="t('insight.mcpServers.fieldEndpoint')"
                    required
                  >
                    <ElInput
                      v-model="endpoint"
                      :placeholder="endpointPlaceholder"
                      @input="clearFieldError('endpoint')"
                    />
                    <p class="mcp-field-hint">
                      {{ t('insight.mcpServers.fieldEndpointHint') }}
                    </p>
                  </ElFormItem>
                </div>

                <ElFormItem
                  class="fullscreen-form-item--in-card fullscreen-form-status-item"
                  :label="t('insight.mcpServers.fieldEnabled')"
                >
                  <div class="mcp-enabled-row">
                    <ElSwitch v-model="enabled" />
                    <span class="mcp-field-hint mcp-field-hint--inline">
                      {{ t('insight.mcpServers.fieldEnabledHint') }}
                    </span>
                  </div>
                </ElFormItem>
              </ElForm>
            </section>

            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.mcpServers.sectionConfig') }}
              </h3>
              <p class="mcp-section-desc">
                {{ t('insight.mcpServers.sectionConfigDesc') }}
              </p>

              <div class="mcp-config-panel">
                <div
                  v-for="(row, index) in configRows"
                  :key="index"
                  class="mcp-config-row"
                >
                  <ElInput
                    v-model="row.key"
                    :placeholder="t('insight.mcpServers.fieldConfigKey')"
                  />
                  <ElInput
                    v-model="row.value"
                    :placeholder="t('insight.mcpServers.fieldConfigValue')"
                  />
                  <button
                    type="button"
                    class="mcp-config-row__remove"
                    :aria-label="t('common.delete')"
                    @click="removeConfigRow(index)"
                  >
                    <Trash2 :size="16" />
                  </button>
                </div>
                <ElButton
                  size="small"
                  class="mcp-config-add"
                  @click="addConfigRow"
                >
                  <Plus :size="14" />
                  {{ t('insight.mcpServers.addConfigRow') }}
                </ElButton>
              </div>
              <p class="mcp-field-hint">
                {{ t('insight.mcpServers.fieldConfigHint') }}
              </p>
            </section>
          </div>
        </div>
      </div>

      <footer class="fullscreen-form-footer">
        <ElButton
          :disabled="saving"
          @click="handleBack"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :loading="saving"
          :disabled="saving || loading"
          @click="handleSubmit"
        >
          {{ isEditing ? t('common.save') : t('insight.mcpServers.btnCreate') }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.mcp-form-fullscreen .fullscreen-form-page {
  min-height: calc(var(--app-viewport-height) - var(--app-header-height));
}

.mcp-form-fullscreen .fullscreen-form-card,
.mcp-form-fullscreen .fullscreen-form-step-stack {
  overflow: visible;
}

.mcp-section-desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-secondary);
}

.mcp-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.mcp-field-hint--inline {
  margin: 0;
}

.mcp-enabled-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.mcp-connection-grid {
  margin-bottom: 14px;
}

.mcp-config-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: var(--color-bg-muted, #f8fafc);
}

.mcp-config-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.mcp-config-row__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  background: #fff;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.mcp-config-row__remove:hover {
  color: var(--color-danger, #dc2626);
  border-color: var(--color-danger, #dc2626);
}

.mcp-config-add {
  align-self: flex-start;
}

@media (max-width: 720px) {
  .mcp-config-row {
    grid-template-columns: 1fr;
  }

  .mcp-config-row__remove {
    width: 100%;
  }
}
</style>
