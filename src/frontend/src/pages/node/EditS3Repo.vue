<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, CircleAlert, Lock, ShieldCheck, Wrench } from 'lucide-vue-next'
import {
  getStorageRepository,
  updateStorageRepository,
  verifyStorageRepositoryAccess,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import {
  s3EndpointDisplay,
  s3PlatformLabelKey,
} from '../../lib/s3PlatformDisplay'
import S3PlatformBrandIcon from '../../components/S3PlatformBrandIcon.vue'
import { apiErrorMessage } from '../../lib/api'
import {
  defaultS3UrlStyle,
  normalizeS3UrlStyle,
  type S3StoragePlatform as StoragePlatform,
  type S3UrlStyle,
} from '../../lib/s3ProviderProfiles'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import {
  REPOSITORY_QUOTA_UNITS,
  normalizeRepositoryQuotaUnit,
  repositoryQuotaToGb,
  repositoryQuotaValueFromGb,
  type RepositoryQuotaUnit,
} from '../../lib/repositoryQuota'


const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const repositoryId = computed(() => Number(route.params.id))
const loading = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
type SavingPhase = 'verifying' | 'saving'
const busy = ref(false)
const savingPhase = ref<SavingPhase | null>(null)
const repo = ref<StorageRepository | null>(null)

/* locked fields */
const platform = ref<StoragePlatform>('custom')
const platformLabelText = computed(() => t(s3PlatformLabelKey(platform.value)))
const bucket = ref('')
const endpoint = ref('')
const prefix = ref('')
const endpointDisplay = computed(() => s3EndpointDisplay(endpoint.value))
const prefixDisplay = computed(() => prefix.value || '\u2014')

/* editable fields */
const name = ref('')
const region = ref('')
const s3UrlStyle = ref<S3UrlStyle>(defaultS3UrlStyle('custom'))
const useTls = ref(true)
const quotaGb = ref(0)
const quotaUnit = ref<RepositoryQuotaUnit>('GB')
const quotaAlertEnabled = ref(false)
const quotaAlertThreshold = ref<number>(80)

/* credentials (masked by default, can be rewritten) */
const credentialMask = '\u2022\u2022\u2022\u2022\u2022\u2022'
const hasAccessKey = ref(false)
const hasSecret = ref(false)
const accessKeyRewriting = ref(false)
const secretRewriting = ref(false)
const accessKeyDraft = ref('')
const secretDraft = ref('')
const accessKeyMasked = computed(() => (hasAccessKey.value ? credentialMask : '\u2014'))
const secretMasked = computed(() => (hasSecret.value ? credentialMask : '\u2014'))

/* original values for change detection */
const originS3UrlStyle = ref<S3UrlStyle>(defaultS3UrlStyle('custom'))
const originUseTls = ref(true)

/* verify-access state machine */
type VerifyStatus = 'idle' | 'verifying' | 'success' | 'failed'
const verifyStatus = ref<VerifyStatus>('idle')
const verifyDetail = ref('')

const authChanged = computed(() => {
  if (accessKeyRewriting.value || secretRewriting.value) return true
  if (s3UrlStyle.value !== originS3UrlStyle.value) return true
  if (useTls.value !== originUseTls.value) return true
  return false
})

const urlStyleLabel = computed(() =>
  s3UrlStyle.value === 'auto'
    ? t('addS3Repo.s3UrlStyleAuto')
    : s3UrlStyle.value === 'virtual_hosted'
      ? t('addS3Repo.s3UrlStyleVirtualHosted')
      : t('addS3Repo.s3UrlStylePath'),
)

const quotaThresholdValid = computed(() => {
  if (!quotaAlertEnabled.value) return true
  const v = Number(quotaAlertThreshold.value)
  return Number.isFinite(v) && v >= 1 && v <= 99
})

const urlStyleOptions = computed(() => [
  { value: 'virtual_hosted', label: t('addS3Repo.s3UrlStyleVirtualHosted') },
  { value: 'path', label: t('addS3Repo.s3UrlStylePath') },
])

async function load() {
  if (!repositoryId.value || Number.isNaN(repositoryId.value)) {
    ElMessage.error({ message: t('repositoriesPage.editS3Repo.loadFailed'), grouping: true })
    router.replace('/node/repositories')
    return
  }
  loading.value = true
  try {
    const data = await getStorageRepository(repositoryId.value)
    repo.value = data
    hydrate(data)
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('repositoriesPage.editS3Repo.loadFailed')), grouping: true })
    router.replace('/node/repositories')
  } finally {
    loading.value = false
  }
}

function hydrate(data: StorageRepository) {
  const cfg = (data.config || {}) as Record<string, unknown>
  name.value = data.name || ''
  const plat = String(data.s3_platform || 'custom').toLowerCase()
  platform.value = (
    ['aliyun', 'huaweicloud', 'aws', 'custom'].includes(plat)
      ? (plat as StoragePlatform)
      : 'custom'
  )
  bucket.value = (data.s3_bucket as string) || ''
  endpoint.value = (cfg.endpoint as string) || ''
  prefix.value = (cfg.prefix as string) || ''
  region.value = (cfg.region as string) || ''
  const loadedUrlStyle = normalizeS3UrlStyle(cfg.s3_url_style, platform.value)
  s3UrlStyle.value = loadedUrlStyle === 'auto' ? 'virtual_hosted' : loadedUrlStyle
  useTls.value = cfg.use_tls !== false
  quotaUnit.value = normalizeRepositoryQuotaUnit(cfg.quota_unit)
  quotaGb.value = repositoryQuotaValueFromGb(cfg.quota_gb, quotaUnit.value)
  quotaAlertEnabled.value = Boolean(cfg.quota_alert_enabled)
  quotaAlertThreshold.value = Number(cfg.quota_alert_threshold || 80)
  hasAccessKey.value = Boolean(String(cfg.access_key_id || '').trim())
  hasSecret.value = Boolean(String(cfg.secret_access_key || '').trim())
  accessKeyRewriting.value = false
  secretRewriting.value = false
  accessKeyDraft.value = ''
  secretDraft.value = ''
  originS3UrlStyle.value = s3UrlStyle.value
  originUseTls.value = useTls.value
  verifyStatus.value = 'idle'
  verifyDetail.value = ''
  savingPhase.value = null
}

function startRewriteAccessKey() {
  accessKeyRewriting.value = true
  accessKeyDraft.value = ''
}
function startRewriteSecret() {
  secretRewriting.value = true
  secretDraft.value = ''
}
function cancelRewriteAccessKey() {
  accessKeyRewriting.value = false
  accessKeyDraft.value = ''
}
function cancelRewriteSecret() {
  secretRewriting.value = false
  secretDraft.value = ''
}

function buildPayload() {
  const config: Record<string, unknown> = {
    quota_gb: repositoryQuotaToGb(quotaGb.value, quotaUnit.value),
    quota_unit: quotaUnit.value,
    quota_alert_enabled: quotaAlertEnabled.value,
    quota_alert_threshold: quotaAlertEnabled.value ? Number(quotaAlertThreshold.value || 0) : 0,
  }
  if (s3UrlStyle.value !== originS3UrlStyle.value) {
    config.s3_url_style = s3UrlStyle.value
  }
  if (useTls.value !== originUseTls.value) {
    config.use_tls = useTls.value
  }
  if (accessKeyRewriting.value && accessKeyDraft.value.trim()) {
    config.access_key_id = accessKeyDraft.value.trim()
  }
  if (secretRewriting.value && secretDraft.value) {
    config.secret_access_key = secretDraft.value
  }
  Object.keys(config).forEach((k) => {
    if (config[k] === undefined) delete config[k]
  })
  return { name: name.value.trim(), config }
}

function buildVerifyOverrides(): {
  region?: string
  s3_url_style?: S3UrlStyle
  use_tls?: boolean
  access_key_id?: string
  secret_access_key?: string
} {
  const overrides: {
    region?: string
    s3_url_style?: S3UrlStyle
    use_tls?: boolean
    access_key_id?: string
    secret_access_key?: string
  } = {
    region: region.value.trim() || undefined,
    s3_url_style: s3UrlStyle.value,
    use_tls: useTls.value,
  }
  if (accessKeyRewriting.value && accessKeyDraft.value.trim()) {
    overrides.access_key_id = accessKeyDraft.value.trim()
  }
  if (secretRewriting.value && secretDraft.value) {
    overrides.secret_access_key = secretDraft.value
  }
  Object.keys(overrides).forEach((k) => {
    if (overrides[k as keyof typeof overrides] === undefined) {
      delete overrides[k as keyof typeof overrides]
    }
  })
  return overrides
}

async function runVerify(): Promise<boolean> {
  // Keep the draft in place and surface a stable failure beside the fields.
  verifyDetail.value = ''
  try {
    const result = await verifyStorageRepositoryAccess(repositoryId.value, buildVerifyOverrides())
    if (result.ok) {
      return true
    }
    verifyDetail.value = result.message || t('repositoriesPage.editS3Repo.verifyFailed')
    return false
  } catch (err) {
    verifyDetail.value = apiErrorMessage(err, t('repositoriesPage.editS3Repo.verifyFailed'))
    return false
  }
}

async function onSave() {
  if (busy.value) return
  if (!validateInline([
    { field: 'name', message: t('repositoriesPage.editS3Repo.errName'), valid: !!name.value.trim() },
    { field: 'quotaThreshold', message: t('repositoriesPage.editS3Repo.errQuotaAlertThreshold'), valid: quotaThresholdValid.value },
  ])) return

  // When the connection or credentials changed, run an explicit verify step
  // user sees what is happening. On failure we keep the page editable and
  // surface the detail in the in-page banner; the user can correct the
  // fields and click save again to retry.
  if (authChanged.value) {
    busy.value = true
    savingPhase.value = 'verifying'
    const ok = await runVerify()
    if (!ok) {
      verifyStatus.value = 'failed'
      busy.value = false
      savingPhase.value = null
      return
    }
  }

  busy.value = true
  savingPhase.value = 'saving'
  try {
    await updateStorageRepository(repositoryId.value, buildPayload())
    ElMessage.success({ message: t('repositoriesPage.editS3Repo.msgUpdated'), grouping: true })
    router.push({ path: '/node/repositories', query: { tab: 's3' } })
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessage(err, t('repositoriesPage.editS3Repo.saveFailed')),
      grouping: true,
    })
  } finally {
    busy.value = false
    savingPhase.value = null
  }
}

function handleBack() {
  router.push({ path: '/node/repositories', query: { tab: 's3' } })
}

onMounted(load)
watch(repositoryId, (id) => {
  if (id) load()
})
</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen"
  >
    <div class="fullscreen-form-page add-s3-page edit-s3-page">
      <div class="fullscreen-form-header">
        <button
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
            <Wrench
              :size="18"
              class="inline-block align-[-3px] mr-1 text-[rgb(37_99_235)]"
            />
            {{ t('repositoriesPage.editS3Repo.pageTitle') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('repositoriesPage.editS3Repo.pageDesc') }}
          </p>
        </div>
      </div>

      <div
        v-if="loading"
        class="edit-s3-loading"
      >
        {{ t('common.loading') || 'Loading\u2026' }}
      </div>

      <div
        v-else-if="repo"
        class="fullscreen-form-layout"
      >
        <!-- Main Form Area -->
        <div class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <!-- 1. Connection and authentication -->
            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('repositoriesPage.editS3Repo.sectionConnectionAuth') }}
                </h3>

                <div class="fullscreen-form-grid">
                  <!-- Endpoint (locked) -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label edit-s3-locked-label">
                      {{ t('addS3Repo.fieldEndpoint') }}
                      <span class="edit-s3-locked-badge edit-s3-locked-badge--inline">
                        <Lock :size="11" />
                        {{ t('repositoriesPage.editS3Repo.lockedBadge') }}
                      </span>
                    </label>
                    <ElInput
                      :model-value="endpointDisplay"
                      class="add-s3-element-field edit-s3-locked-input"
                      readonly
                      disabled
                    />
                  </div>

                  <!-- Region (locked) -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label edit-s3-locked-label">
                      {{ t('addS3Repo.fieldRegion') }}
                      <span class="edit-s3-locked-badge edit-s3-locked-badge--inline">
                        <Lock :size="11" />
                        {{ t('repositoriesPage.editS3Repo.lockedBadge') }}
                      </span>
                    </label>
                    <ElInput
                      :model-value="region"
                      class="add-s3-element-field edit-s3-locked-input"
                      readonly
                      disabled
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintRegion') }}
                    </p>
                  </div>

                  <!-- Access Key (masked, rewrite to update) -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label">{{ t('addS3Repo.fieldAccessKey') }}</label>
                    <div
                      v-if="!accessKeyRewriting"
                      class="edit-s3-credential"
                    >
                      <ElInput
                        :model-value="accessKeyMasked"
                        class="add-s3-element-field edit-s3-locked-input"
                        readonly
                        disabled
                      />
                      <ElButton
                        size="small"
                        @click="startRewriteAccessKey"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnRewrite') }}
                      </ElButton>
                    </div>
                    <div
                      v-else
                      class="edit-s3-credential"
                    >
                      <ElInput
                        v-model="accessKeyDraft"
                        class="add-s3-element-field"
                        :placeholder="t('repositoriesPage.phAccessKey')"
                      />
                      <ElButton
                        size="small"
                        @click="cancelRewriteAccessKey"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnCancel') }}
                      </ElButton>
                    </div>
                  </div>

                  <!-- Secret Key (masked, rewrite to update) -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label">{{ t('addS3Repo.fieldSecretKey') }}</label>
                    <div
                      v-if="!secretRewriting"
                      class="edit-s3-credential"
                    >
                      <ElInput
                        :model-value="secretMasked"
                        type="password"
                        class="add-s3-element-field edit-s3-locked-input"
                        readonly
                        disabled
                      />
                      <ElButton
                        size="small"
                        @click="startRewriteSecret"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnRewrite') }}
                      </ElButton>
                    </div>
                    <div
                      v-else
                      class="edit-s3-credential"
                    >
                      <ElInput
                        v-model="secretDraft"
                        type="password"
                        show-password
                        class="add-s3-element-field"
                        :placeholder="t('repositoriesPage.phSecretKey')"
                      />
                      <ElButton
                        size="small"
                        @click="cancelRewriteSecret"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnCancel') }}
                      </ElButton>
                    </div>
                  </div>

                  <!-- URL Style (editable) -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label">
                      {{ t('addS3Repo.fieldS3UrlStyle') }}
                    </label>
                    <ElSelect
                      v-model="s3UrlStyle"
                      class="add-s3-element-field"
                    >
                      <ElOption
                        v-for="opt in urlStyleOptions"
                        :key="opt.value"
                        :label="opt.label"
                        :value="opt.value"
                      />
                    </ElSelect>
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintS3UrlStyle') }}
                    </p>
                  </div>

                  <!-- TLS (editable) -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label fullscreen-form-field__label--toggle">
                      {{ t('addS3Repo.fieldUseTls') }}
                    </label>
                    <div class="add-s3-toggle">
                      <ElSwitch v-model="useTls" />
                      <span class="fullscreen-form-field__hint add-s3-toggle__label">
                        {{ useTls ? t('addS3Repo.tlsOnHint') : t('addS3Repo.tlsOffHint') }}
                      </span>
                    </div>
                  </div>
                </div>

                <div
                  v-if="verifyStatus === 'failed' && verifyDetail"
                  class="edit-s3-auth-error"
                  role="alert"
                >
                  <CircleAlert :size="18" />
                  <span>{{ verifyDetail }}</span>
                </div>
              </section>
            </div>

            <!-- 2. Repository configuration -->
            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('addS3Repo.stepRepo') }}
                </h3>

                <div class="fullscreen-form-grid">
                  <div class="add-s3-repo-primary-fields">
                    <!-- Repo Name -->
                    <div
                      data-validation-field="name"
                      class="fullscreen-form-field"
                    >
                      <label class="fullscreen-form-field__label edit-s3-locked-label">
                        {{ t('addS3Repo.fieldRepoName') }}
                        <span class="fullscreen-form-field__required">*</span>
                      </label>
                      <ElInput
                        v-model="name"
                        class="add-s3-element-field add-s3-repo-primary-input"
                        :placeholder="t('repositoriesPage.phRepoName')"
                        @input="clearFieldError('name')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('addS3Repo.hintRepoName') }}
                      </p>
                      <p
                        v-if="errors.name"
                        class="el-form-item__error"
                      >
                        {{ errors.name }}
                      </p>
                    </div>

                    <!-- Bucket (locked) -->
                    <div class="fullscreen-form-field">
                      <label class="fullscreen-form-field__label edit-s3-locked-label">
                        {{ t('addS3Repo.fieldBucket') }}
                        <span class="edit-s3-locked-badge edit-s3-locked-badge--inline">
                          <Lock :size="11" />
                          {{ t('repositoriesPage.editS3Repo.lockedBadge') }}
                        </span>
                      </label>
                      <div class="add-s3-bucket-tabs edit-s3-bucket-tabs--locked">
                        <button
                          class="add-s3-bucket-tab add-s3-bucket-tab--active"
                          disabled
                        >
                          {{ t('addS3Repo.fieldBucketExisting') }}
                        </button>
                      </div>
                      <ElInput
                        :model-value="bucket"
                        class="add-s3-element-field add-s3-repo-primary-input edit-s3-locked-input"
                        readonly
                        disabled
                      />
                    </div>

                    <!-- Prefix (locked) -->
                    <div class="fullscreen-form-field">
                      <label class="fullscreen-form-field__label">
                        {{ t('addS3Repo.fieldPrefix') }}
                        <span class="edit-s3-locked-badge edit-s3-locked-badge--inline">
                          <Lock :size="11" />
                          {{ t('repositoriesPage.editS3Repo.lockedBadge') }}
                        </span>
                      </label>
                      <ElInput
                        :model-value="prefixDisplay"
                        class="add-s3-element-field add-s3-repo-primary-input edit-s3-locked-input"
                        readonly
                        disabled
                      />
                    </div>
                  </div>

                  <!-- Quota -->
                  <div class="add-s3-quota-pair">
                    <div class="fullscreen-form-field add-s3-quota-pair__col">
                      <label class="fullscreen-form-field__label add-s3-quota-pair__head">
                        {{ t('addS3Repo.fieldQuota') }}
                      </label>
                      <div class="add-s3-quota-pair__control">
                        <div class="hfl-detail-form-input hfl-detail-form-input--narrow add-s3-quota-pair__input repository-quota-split-input repository-quota-split-input--edit">
                          <ElInputNumber
                            v-model="quotaGb"
                            class="hfl-detail-form-input__num"
                            :placeholder="t('addS3Repo.phQuota')"
                            :min="0"
                            :precision="0"
                            :step="1"
                            controls-position="right"
                          />
                          <ElSelect
                            v-model="quotaUnit"
                            class="hfl-detail-form-input__unit"
                            :aria-label="t('repositoriesPage.quotaUnit')"
                          >
                            <ElOption
                              v-for="unit in REPOSITORY_QUOTA_UNITS"
                              :key="unit"
                              :label="unit"
                              :value="unit"
                            />
                          </ElSelect>
                        </div>
                      </div>
                      <p class="fullscreen-form-field__hint">
                        {{ t('addS3Repo.hintQuota') }}
                      </p>
                    </div>

                    <div
                      data-validation-field="quotaThreshold"
                      class="fullscreen-form-field add-s3-quota-pair__col add-s3-quota-alert-field"
                    >
                      <div class="fullscreen-form-field__label add-s3-quota-pair__head add-s3-quota-alert-head">
                        <ElCheckbox v-model="quotaAlertEnabled">
                          {{ t('addS3Repo.fieldQuotaAlert') }}
                        </ElCheckbox>
                      </div>
                      <div class="add-s3-quota-pair__control">
                        <div class="hfl-detail-form-input hfl-detail-form-input--narrow add-s3-quota-pair__input">
                          <ElInputNumber
                            v-model="quotaAlertThreshold"
                            class="hfl-detail-form-input__num"
                            :min="1"
                            :max="99"
                            :step="1"
                            :disabled="!quotaAlertEnabled"
                            :placeholder="t('repositoriesPage.phQuotaAlertThreshold')"
                            controls-position="right"
                            @change="clearFieldError('quotaThreshold')"
                          />
                          <div class="hfl-detail-form-input__suffix">
                            %
                          </div>
                        </div>
                      </div>
                      <p class="fullscreen-form-field__hint">
                        {{ t('addS3Repo.hintQuotaAlertThreshold') }}
                      </p>
                      <p
                        v-if="errors.quotaThreshold"
                        class="el-form-item__error"
                      >
                        {{ errors.quotaThreshold }}
                      </p>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </div>

          <div class="fullscreen-form-footer add-s3-footer">
            <ElButton
              :disabled="busy"
              @click="handleBack"
            >
              {{ t('repositoriesPage.btnCancel') }}
            </ElButton>
            <ElButton
              type="primary"
              :loading="busy"
              :disabled="busy"
              @click="onSave"
            >
              <template v-if="busy && savingPhase === 'verifying'">
                {{ t('repositoriesPage.editS3Repo.savingAndVerifying') }}
              </template>
              <template v-else-if="busy && savingPhase === 'saving'">
                {{ t('repositoriesPage.editS3Repo.saving') }}
              </template>
              <template v-else>
                {{ t('repositoriesPage.editS3Repo.btnSave') }}
              </template>
            </ElButton>
          </div>
        </div>

        <!-- Preview Sidebar -->
        <aside class="fullscreen-form-sidebar add-form-preview-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon">
                <S3PlatformBrandIcon
                  :platform="platform"
                  :size="28"
                  :alt="platformLabelText"
                  icon-class="add-form-preview-header__icon-img"
                  lucide-class="add-form-preview-header__icon-lucide"
                />
              </div>
              <div class="add-form-preview-header__info">
                <h4 class="add-form-preview-header__name">
                  {{ name || t('addS3Repo.previewUnnamed') }}
                </h4>
                <p class="add-form-preview-header__type">
                  {{ platformLabelText }}
                </p>
              </div>
            </div>

            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('addS3Repo.previewConnectionAuth') }}
                </h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label edit-s3-preview-lock-label">
                    {{ t('addS3Repo.fieldEndpoint') }}
                    <Lock
                      :size="12"
                      class="edit-s3-row-lock"
                    />
                  </span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !endpoint }"
                  >
                    {{ endpointDisplay }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldRegion') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !region }"
                  >
                    {{ region || '\u2014' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldAccessKey') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--mono': true }"
                  >
                    {{ accessKeyMasked }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldSecretKey') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--mono': true }"
                  >
                    {{ secretMasked }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldS3UrlStyle') }}</span>
                  <span class="add-form-preview-row__value">{{ urlStyleLabel }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldUseTls') }}</span>
                  <span
                    class="add-form-preview-row__value add-form-preview-row__value--badge"
                    :class="useTls ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'"
                  >
                    <ShieldCheck
                      v-if="useTls"
                      class="add-form-preview-row__shield"
                      :size="14"
                    />
                    {{ useTls ? 'HTTPS' : 'HTTP' }}
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('addS3Repo.previewRepoConfig') }}
                </h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label edit-s3-preview-lock-label">
                    {{ t('addS3Repo.fieldBucket') }}
                    <Lock
                      :size="12"
                      class="edit-s3-row-lock"
                    />
                  </span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !bucket }"
                  >
                    {{ bucket || '\u2014' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label edit-s3-preview-lock-label">
                    {{ t('addS3Repo.fieldPrefix') }}
                    <Lock
                      :size="12"
                      class="edit-s3-row-lock"
                    />
                  </span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !prefix }"
                  >
                    {{ prefixDisplay }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldQuota') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--highlight': quotaGb > 0 }"
                  >
                    {{ quotaGb > 0 ? `${quotaGb} ${quotaUnit}` : t('addS3Repo.previewUnlimited') }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldQuotaAlert') }}</span>
                  <span
                    class="add-form-preview-row__value add-form-preview-row__value--badge"
                    :class="quotaAlertEnabled ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'"
                  >
                    <span
                      v-if="quotaAlertEnabled"
                      class="add-form-preview-row__dot add-form-preview-row__dot--green"
                    />
                    <template v-if="quotaAlertEnabled">
                      {{ t('repositoriesPage.enabled') }} ({{ quotaAlertThreshold }}%)
                    </template>
                    <template v-else>
                      {{ t('repositoriesPage.disabled') }}
                    </template>
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

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
<style scoped>
/* Override add-s3 layout paddings to suit edit page; reuse add-s3-platform-btn styling */
.edit-s3-page .fullscreen-form-main { padding-bottom: 0; }
.edit-s3-loading { padding: 32px; color: var(--el-text-color-secondary); }

.edit-s3-auth-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 14px;
  color: var(--el-color-danger);
  background: var(--color-danger-light);
  border: 1px solid color-mix(in srgb, var(--el-color-danger) 35%, transparent);
  border-radius: 8px;
  line-height: 1.5;
}

.edit-s3-auth-error svg { flex: 0 0 auto; margin-top: 2px; }

.edit-s3-locked-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
}

.edit-s3-locked-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 18px;
  margin-left: 0;
  padding: 0 7px;
  font-size: 11px;
  line-height: 18px;
  color: var(--el-color-warning);
  background: var(--color-warning-light);
  border-radius: 999px;
  font-weight: 400;
  white-space: nowrap;
}
.edit-s3-locked-badge svg {
  flex-shrink: 0;
}
.edit-s3-locked-badge--inline { margin-left: 0; }
.edit-s3-bucket-tabs--locked { pointer-events: none; }
.edit-s3-bucket-tabs--locked .add-s3-bucket-tab { cursor: default; }
.edit-s3-locked-input :deep(.el-input__wrapper) {
  background: var(--el-fill-color-light);
  box-shadow: none;
}
.edit-s3-locked-input :deep(.el-input__inner) {
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-monospace, ui-monospace, SFMono-Regular, monospace);
}

.edit-s3-credential { display: flex; align-items: center; gap: 8px; }
.edit-s3-credential :deep(.el-input) { flex: 1; }

.edit-s3-preview-lock-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.edit-s3-row-lock {
  flex-shrink: 0;
  color: var(--el-text-color-placeholder);
}


.edit-s3-verify-dialog__body {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: var(--el-text-color-primary);
}
.edit-s3-verify-dialog__body--column {
  flex-direction: column;
  align-items: stretch;
  gap: 14px;
}
.edit-s3-verify-dialog__row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.edit-s3-verify-dialog__icon { flex-shrink: 0; }
.edit-s3-verify-dialog__icon--spinning {
  color: var(--el-color-primary);
  animation: edit-s3-verify-dialog__spin 1s linear infinite;
}
.edit-s3-verify-dialog__icon--success { color: var(--el-color-success); }
.edit-s3-verify-dialog__icon--failed { color: var(--el-color-danger); }
.edit-s3-verify-dialog__text {
  font-size: 14px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}
.edit-s3-verify-dialog__section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 10px 12px;
}
.edit-s3-verify-dialog__section-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.edit-s3-verify-dialog__section-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  white-space: pre-wrap;
  word-break: break-word;
}
.edit-s3-verify-dialog__error-summary {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  color: var(--el-color-danger);
  background: var(--color-error-light);
  border: 1px solid var(--color-error-border);
  border-radius: 6px;
}
.edit-s3-verify-dialog__error-summary-label {
  flex: 0 0 auto;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-color-danger-dark-2);
}
.edit-s3-verify-dialog__error-summary-text {
  min-width: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  word-break: break-word;
}
.edit-s3-verify-dialog__raw {
  margin: 0;
  padding: 9px 10px;
  font-family: var(--el-font-family-monospace, ui-monospace, SFMono-Regular, monospace);
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-regular);
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow: auto;
}
@keyframes edit-s3-verify-dialog__spin {
  to { transform: rotate(360deg); }
}
</style>
