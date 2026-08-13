<script setup lang="ts">
/**
 * --------------------------------------------------------------------------
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, FolderTree, Lock, Wrench } from 'lucide-vue-next'
import { apiErrorMessage } from '../../lib/api'
import { getNode } from '../../lib/nodeApi'
import {
  getStorageRepository,
  updateStorageRepository,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

/* ---------- route ---------- */
const repositoryId = computed(() => Number(route.params.id))

/* ---------- form state ---------- */
const loading = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
const busy = ref(false)
const repo = ref<StorageRepository | null>(null)

/* editable */
const name = ref('')
/* quota values read directly from `config` so we can build the PATCH payload */
const quotaGb = ref(0)
const quotaAlertEnabled = ref(false)
const quotaAlertThreshold = ref<number>(80)

/* read-only mirrors for change detection */
const originName = ref('')
const originQuotaGb = ref(0)
const originQuotaAlertEnabled = ref(false)
const originQuotaAlertThreshold = ref(80)

const fallbackNode = ref<{ name: string; ip: string } | null>(null)

/* ---------- computed ---------- */
const proxyNodeName = computed(() => {
  const r = repo.value
  if (!r) return ''
  const cfg = (r.config || {}) as Record<string, unknown>
  return (
    String(cfg.proxy_node_name || cfg.bind_node_name || r.bind_node_display_name || '').trim() ||
    fallbackNode.value?.name ||
    ''
  )
})

const proxyNodeIp = computed(() => {
  const r = repo.value
  if (!r) return ''
  const cfg = (r.config || {}) as Record<string, unknown>
  return (
    String(cfg.proxy_node_ip || cfg.bind_node_ip || r.bind_node_ip || '').trim() ||
    fallbackNode.value?.ip ||
    ''
  )
})

const proxyNodeLabel = computed(() => {
  const name = proxyNodeName.value
  const ip = proxyNodeIp.value
  if (name && ip) return `${name} (${ip})`
  if (name) return name
  if (ip) return ip
  const id = repo.value?.bind_node_id
  return id ? `Proxy node #${id}` : '—'
})

const proxyNodeDir = computed(() => {
  const r = repo.value
  if (!r) return ''
  const cfg = (r.config || {}) as Record<string, unknown>
  return String(cfg.proxy_node_dir || '').trim()
})

const validQuotaAlertThreshold = computed(() => {
  if (!quotaAlertEnabled.value) return true
  const v = Number(quotaAlertThreshold.value || 0)
  return Number.isFinite(v) && v >= 1 && v <= 99
})

const dirty = computed(() => {
  if (name.value.trim() !== originName.value) return true
  if (Number(quotaGb.value || 0) !== originQuotaGb.value) return true
  if (Boolean(quotaAlertEnabled.value) !== originQuotaAlertEnabled.value) return true
  if (quotaAlertEnabled.value && Number(quotaAlertThreshold.value || 0) !== originQuotaAlertThreshold.value) return true
  return false
})

/* ---------- load ---------- */
async function load() {
  if (!repositoryId.value || Number.isNaN(repositoryId.value)) {
    ElMessage.error({ message: t('repositoriesPage.editProxyFsRepo.loadFailed'), grouping: true })
    router.replace('/node/repositories')
    return
  }
  loading.value = true
  try {
    const data = await getStorageRepository(repositoryId.value)
    if (data && data.repo_type === 'proxy_fs') {
      repo.value = data
      hydrate(data)
      await maybeFetchProxyNode(data)
    } else {
      ElMessage.error({ message: t('repositoriesPage.editProxyFsRepo.loadFailed'), grouping: true })
      router.replace('/node/repositories')
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('repositoriesPage.editProxyFsRepo.loadFailed')), grouping: true })
    router.replace('/node/repositories')
  } finally {
    loading.value = false
  }
}

async function maybeFetchProxyNode(data: StorageRepository) {
  const cfg = (data.config || {}) as Record<string, unknown>
  const hasName =
    String(cfg.proxy_node_name || cfg.bind_node_name || data.bind_node_display_name || '').trim() !== ''
  const hasIp =
    String(cfg.proxy_node_ip || cfg.bind_node_ip || data.bind_node_ip || '').trim() !== ''
  if (hasName && hasIp) {
    fallbackNode.value = null
    return
  }
  const rawId = cfg.bind_node_id ?? data.bind_node_id
  const id = Number(rawId)
  if (!Number.isFinite(id) || id <= 0) return
  try {
    const node = await getNode(id)
    fallbackNode.value = {
      name: String(node.name || '').trim(),
      ip: String(node.ip_address || '').trim(),
    }
  } catch {
    fallbackNode.value = null
  }
}

function hydrate(data: StorageRepository) {
  const cfg = (data.config || {}) as Record<string, unknown>
  name.value = data.name || ''
  quotaGb.value = Number(cfg.quota_gb || 0)
  quotaAlertEnabled.value = Boolean(cfg.quota_alert_enabled)
  quotaAlertThreshold.value = Number(cfg.quota_alert_threshold || 80)
  originName.value = name.value
  originQuotaGb.value = quotaGb.value
  originQuotaAlertEnabled.value = quotaAlertEnabled.value
  originQuotaAlertThreshold.value = quotaAlertThreshold.value
}

/* ---------- save ---------- */
function buildPayload() {
  const config: Record<string, unknown> = {
    quota_gb: Number(quotaGb.value || 0),
    quota_alert_enabled: Boolean(quotaAlertEnabled.value),
    quota_alert_threshold: quotaAlertEnabled.value
      ? Number(quotaAlertThreshold.value || 0)
      : 0,
  }
  Object.keys(config).forEach((k) => {
    if (config[k] === undefined) delete config[k]
  })
  return { name: name.value.trim(), config }
}

async function onSave() {
  if (busy.value) return
  if (!validateInline([
    { field: 'name', message: t('repositoriesPage.editProxyFsRepo.errName'), valid: !!name.value.trim() },
    { field: 'quotaThreshold', message: t('repositoriesPage.editProxyFsRepo.errQuotaAlertThreshold'), valid: validQuotaAlertThreshold.value },
  ])) return
  busy.value = true
  try {
    await updateStorageRepository(repositoryId.value, buildPayload())
    ElMessage.success({ message: t('repositoriesPage.editProxyFsRepo.msgUpdated'), grouping: true })
    router.push({ path: '/node/repositories', query: { tab: 'proxy_fs' } })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    busy.value = false
  }
}

function handleBack() {
  router.push({ path: '/node/repositories', query: { tab: 'proxy_fs' } })
}

onMounted(load)
watch(repositoryId, (id) => {
  if (id) void load()
})
</script>

<template>
  <div ref="pageRef" class="fullscreen-form-fullscreen resource-add-fullscreen">
    <div class="fullscreen-form-page add-proxy-fs-page edit-proxy-fs-page">
      <header class="fullscreen-form-header">
        <button
          class="fullscreen-form-header__back"
          type="button"
          @click="handleBack"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            <Wrench :size="18" class="inline-block align-[-3px] mr-1 text-[rgb(37_99_235)]" />
            {{ t('repositoriesPage.editProxyFsRepo.pageTitle') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('repositoriesPage.editProxyFsRepo.pageDesc') }}
          </p>
        </div>
      </header>

      <div
        v-if="loading"
        class="edit-proxy-fs-loading"
      >
        {{ t('common.loading') }}
      </div>

      <div
        v-else-if="repo"
        class="fullscreen-form-layout add-proxy-fs-layout"
      >
        <div class="fullscreen-form-main add-proxy-fs-main">
          <section
            class="add-proxy-fs-section add-proxy-fs-section--card add-proxy-fs-step-section"
          >
            <h3 class="add-proxy-fs-section__title">
              <FolderTree
                :size="16"
                class="add-proxy-fs-section__icon"
              />
              {{ t('repositoriesPage.editProxyFsRepo.sectionRepo') }}
            </h3>

            <ElForm
              label-position="top"
              class="add-proxy-fs-form"
            >
              <ElFormItem
                data-validation-field="name"
                :error="errors.name"
                :label="t('repositoriesPage.editProxyFsRepo.fieldName')"
                required
              >
                <ElInput
                  v-model="name"
                  :placeholder="t('repositoriesPage.phRepoName')"
                  @input="clearFieldError('name')"
                />
              </ElFormItem>

              <ElFormItem>
                <template #label>
                  <span>{{ t('repositoriesPage.fieldProxyNode') }}</span>
                  <span class="edit-proxy-fs-locked-badge edit-proxy-fs-locked-badge--inline">
                    <Lock :size="11" />
                    {{ t('repositoriesPage.editProxyFsRepo.lockedBadge') }}
                  </span>
                </template>
                <ElInput
                  :model-value="proxyNodeLabel"
                  class="edit-proxy-fs-locked-input"
                  readonly
                  disabled
                />
              </ElFormItem>

              <ElFormItem>
                <template #label>
                  <span>{{ t('repositoriesPage.fieldProxyNodeDir') }}</span>
                  <span class="edit-proxy-fs-locked-badge edit-proxy-fs-locked-badge--inline">
                    <Lock :size="11" />
                    {{ t('repositoriesPage.editProxyFsRepo.lockedBadge') }}
                  </span>
                </template>
                <ElInput
                  :model-value="proxyNodeDir || '—'"
                  class="edit-proxy-fs-locked-input edit-proxy-fs-locked-input--mono"
                  readonly
                  disabled
                />
              </ElFormItem>
            </ElForm>
          </section>

          <section
            class="add-proxy-fs-section add-proxy-fs-section--card add-proxy-fs-step-section"
          >
            <h3 class="add-proxy-fs-section__title">
              {{ t('repositoriesPage.fieldQuota') }}
            </h3>
            <ElForm
              label-position="top"
              class="add-proxy-fs-form"
            >
              <div class="add-proxy-fs-form-row add-proxy-fs-form-row--responsive">
                <div class="fullscreen-form-field add-proxy-fs-quota-col">
                  <label class="fullscreen-form-field__label add-proxy-fs-quota-head">
                    {{ t('repositoriesPage.fieldQuota') }}
                  </label>
                  <div class="add-proxy-fs-quota-control">
                    <div class="hfl-detail-form-input hfl-detail-form-input--narrow add-proxy-fs-quota-input">
                      <ElInputNumber
                        v-model="quotaGb"
                        class="hfl-detail-form-input__num"
                        :placeholder="t('repositoriesPage.phQuota')"
                        :min="0"
                        controls-position="right"
                      />
                      <div class="hfl-detail-form-input__suffix">
                        GB
                      </div>
                    </div>
                  </div>
                  <p class="fullscreen-form-field__hint">
                    {{ t('repositoriesPage.hintQuota') }}
                  </p>
                </div>

                <div data-validation-field="quotaThreshold" class="fullscreen-form-field add-proxy-fs-quota-col add-proxy-fs-quota-panel">
                  <div class="fullscreen-form-field__label add-proxy-fs-quota-head add-proxy-fs-quota-title-row">
                    <ElCheckbox v-model="quotaAlertEnabled">
                      {{ t('repositoriesPage.fieldQuotaAlert') }}
                    </ElCheckbox>
                  </div>
                  <div class="add-proxy-fs-quota-control">
                    <div class="hfl-detail-form-input hfl-detail-form-input--narrow add-proxy-fs-quota-input">
                      <ElInputNumber
                        v-model="quotaAlertThreshold"
                        class="hfl-detail-form-input__num"
                        :min="1"
                        :max="100"
                        :disabled="!quotaAlertEnabled"
                        controls-position="right"
                        @change="clearFieldError('quotaThreshold')"
                      />
                      <div class="hfl-detail-form-input__suffix">
                        %
                      </div>
                    </div>
                  </div>
                  <p class="fullscreen-form-field__hint">
                    {{ t('repositoriesPage.hintQuotaAlertThreshold') }}
                  </p>
                  <p v-if="errors.quotaThreshold" class="el-form-item__error">{{ errors.quotaThreshold }}</p>
                </div>
              </div>
            </ElForm>
          </section>

          <div class="fullscreen-form-footer add-proxy-fs-footer">
            <div class="add-nas-footer__actions">
              <ElButton @click="handleBack">
                {{ t('repositoriesPage.editProxyFsRepo.btnCancel') }}
              </ElButton>
              <ElButton
                type="primary"
                :loading="busy"
                :disabled="busy || !dirty"
                @click="onSave"
              >
                {{ t('repositoriesPage.editProxyFsRepo.btnSave') }}
              </ElButton>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
<style scoped>
.edit-proxy-fs-locked-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 8px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--el-color-warning);
  background: var(--color-warning-light);
  border-radius: 999px;
  font-weight: 400;
}
.edit-proxy-fs-locked-badge--inline {
  margin-left: 6px;
  padding: 1px 6px;
}
.edit-proxy-fs-locked-input :deep(.el-input__wrapper) {
  background: var(--el-fill-color-light);
  box-shadow: none;
}
.edit-proxy-fs-locked-input :deep(.el-input__inner) {
  color: var(--el-text-color-secondary);
}
.edit-proxy-fs-locked-input--mono :deep(.el-input__inner) {
  font-family: var(--el-font-family-monospace, ui-monospace, SFMono-Regular, monospace);
}

.edit-proxy-fs-loading {
  padding: 32px;
  color: var(--el-text-color-secondary);
}

.edit-proxy-fs-page .fullscreen-form-main {
  padding-bottom: 0;
}

.edit-proxy-fs-page .add-nas-footer__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.edit-proxy-fs-page .add-proxy-fs-form-row {
  display: flex;
  gap: 16px;
}

.edit-proxy-fs-page .add-proxy-fs-quota-col {
  flex: 1 1 0;
  display: grid;
  grid-template-rows: 22px 32px auto;
  gap: 6px;
  align-content: start;
  min-width: 0;
}

.edit-proxy-fs-page .add-proxy-fs-quota-head {
  display: flex;
  align-items: center;
  min-height: 22px;
  margin: 0;
}

.edit-proxy-fs-page .add-proxy-fs-quota-panel {
  padding-left: 20px;
}

.edit-proxy-fs-page .add-proxy-fs-quota-title-row :deep(.el-checkbox) {
  --el-checkbox-height: 22px;
  align-items: center;
  height: 22px;
  min-height: 22px;
  margin-right: 0;
}

.edit-proxy-fs-page .add-proxy-fs-quota-title-row :deep(.el-checkbox__label) {
  line-height: 22px;
}

.edit-proxy-fs-page .add-proxy-fs-quota-control {
  display: flex;
  align-items: center;
  height: 32px;
}

.edit-proxy-fs-page .add-proxy-fs-quota-input {
  width: 200px;
  max-width: 100%;
}

@media (max-width: 900px) {
  .edit-proxy-fs-page .add-proxy-fs-quota-panel {
    padding-left: 0;
  }
}
</style>
