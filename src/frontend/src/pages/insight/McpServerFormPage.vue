<script setup lang="ts">
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
  <div ref="pageRef" class="fullscreen-form-fullscreen resource-add-fullscreen">
    <div class="fullscreen-form-page">
      <header class="fullscreen-form-header">
        <button type="button" class="fullscreen-form-header__back" @click="handleBack">
          <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">{{ pageTitle }}</h1>
          <p class="fullscreen-form-header__desc">{{ pageDesc }}</p>
        </div>
      </header>

      <div v-loading="loading" class="fullscreen-form-layout">
        <div class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <section class="fullscreen-form-card fullscreen-form-section">
              <ElForm label-position="top" class="fullscreen-form-el-form">
                <ElFormItem data-validation-field="name" :error="errors.name" :label="t('insight.mcpServers.fieldName')" required>
                  <ElInput v-model="name" :placeholder="t('insight.mcpServers.fieldNamePh')" @input="clearFieldError('name')" />
                  <p class="mcp-field-hint">{{ t('insight.mcpServers.fieldNameHint') }}</p>
                </ElFormItem>

                <div class="fullscreen-form-grid mcp-connection-grid">
                  <ElFormItem :label="t('insight.mcpServers.fieldTransport')" required>
                    <ElSelect v-model="transport" class="w-full">
                      <ElOption label="URL" value="url" />
                      <ElOption label="STDIO" value="stdio" />
                    </ElSelect>
                    <p class="mcp-field-hint">{{ t('insight.mcpServers.fieldTransportHint') }}</p>
                  </ElFormItem>
                  <ElFormItem data-validation-field="endpoint" :error="errors.endpoint" :label="t('insight.mcpServers.fieldEndpoint')" required>
                    <ElInput v-model="endpoint" :placeholder="endpointPlaceholder" @input="clearFieldError('endpoint')" />
                    <p class="mcp-field-hint">{{ t('insight.mcpServers.fieldEndpointHint') }}</p>
                  </ElFormItem>
                </div>

                <ElFormItem :label="t('insight.mcpServers.fieldConfig')">
                  <div class="mcp-config-panel">
                    <div v-for="(row, index) in configRows" :key="index" class="mcp-config-row">
                      <ElInput v-model="row.key" :placeholder="t('insight.mcpServers.fieldConfigKey')" />
                      <ElInput v-model="row.value" :placeholder="t('insight.mcpServers.fieldConfigValue')" />
                      <button
                        type="button"
                        class="mcp-config-row__remove"
                        :aria-label="t('common.delete')"
                        @click="removeConfigRow(index)"
                      >
                        <Trash2 :size="16" />
                      </button>
                    </div>
                    <ElButton size="small" @click="addConfigRow">
                      <Plus :size="14" />
                      {{ t('insight.mcpServers.addConfigRow') }}
                    </ElButton>
                  </div>
                  <p class="mcp-field-hint">{{ t('insight.mcpServers.fieldConfigHint') }}</p>
                </ElFormItem>

                <ElFormItem :label="t('insight.mcpServers.fieldEnabled')">
                  <ElSwitch v-model="enabled" />
                  <p class="mcp-field-hint">{{ t('insight.mcpServers.fieldEnabledHint') }}</p>
                </ElFormItem>
              </ElForm>
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
          {{ isEditing ? t('common.save') : t('insight.mcpServers.btnCreate') }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.fullscreen-form-section :deep(.el-form-item__label) {
  color: var(--color-text-title);
  font-weight: 600;
}

.mcp-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.mcp-connection-grid :deep(.el-form-item) {
  margin-bottom: 0;
}

.fullscreen-form-el-form > .el-form-item + .mcp-connection-grid,
.mcp-connection-grid + .el-form-item {
  margin-top: 14px;
}

.mcp-config-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.mcp-config-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
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
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.mcp-config-row__remove:hover {
  color: var(--color-danger, #dc2626);
  border-color: var(--color-danger, #dc2626);
}
</style>
