<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api, apiErrorMessage } from '../../lib/api'
import {
  createStorageRepository,
  storageRepositoryS3BucketMode,
  storageRepositoryCreateErrorMessage,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import { fetchStorageProviderCatalog, type StorageProviderConfig } from '../../lib/storageProviderCatalogApi'
import {
  DEFAULT_S3_OBJECT_PREFIX,
  normalizeS3EndpointInput,
  normalizeS3ObjectPrefix,
  s3EndpointDisplay,
} from '../../lib/s3PlatformDisplay'
import { buildS3RepositoryName } from '../../lib/s3RepositoryName'
import { s3BucketNameError } from '../../lib/s3BucketName'
import {
  REPOSITORY_QUOTA_UNITS,
  repositoryQuotaToGb,
  type RepositoryQuotaUnit,
} from '../../lib/repositoryQuota'
import {
  S3_PROVIDER_OPTIONS,
  defaultS3UrlStyle,
  groupS3RegionPresets,
  isS3ProviderEnabled,
  normalizeS3StoragePlatform,
  type S3PlatformCapability as StoragePlatformCapability,
  type S3PlatformSelection as StoragePlatform,
  type S3RegionPreset as RegionPreset,
  type S3UrlStyle,
} from '../../lib/s3ProviderProfiles'
import S3PlatformBrandIcon from '../../components/S3PlatformBrandIcon.vue'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import {
  ArrowLeft,
  RefreshCw,
  Search,
  ShieldCheck,
} from 'lucide-vue-next'

type S3AuthStatus = 'idle' | 'validating' | 'valid' | 'invalid'

const S3_AUTH_VALIDATE_ENDPOINT = '/api/v1/storage/repositories/validate/s3/'

const { t } = useI18n()
const router = useRouter()
const props = withDefaults(defineProps<{
  embedded?: boolean
}>(), {
  embedded: false,
})
const emit = defineEmits<{
  cancel: []
  created: [payload: StorageRepository]
}>()
const busy = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
const platformSearch = ref('')
const regionSearch = ref('')

const catalogProviders = ref<StorageProviderConfig[]>([])
const catalogLoading = ref(false)
const catalogError = ref('')
const STORAGE_PLATFORMS = computed(() => [
  ...catalogProviders.value
    .map((provider) => ({
      value: provider.id as StoragePlatform,
      label: provider.display_name,
      enabled: provider.enabled,
    })),
  ...S3_PROVIDER_OPTIONS,
])

const storagePlatform = ref<StoragePlatform | undefined>('other')
const platformRegionKey = ref<string | undefined>(undefined)
const endpoint = ref('')
const region = ref('')
const accessKeyId = ref('')
const accessKeySecret = ref('')
const s3UrlStyle = ref<S3UrlStyle>(defaultS3UrlStyle('other'))
const useTls = ref(true)

const repoName = ref('')
const repoNameManuallyEdited = ref(false)
let syncingRepoName = false
const bucketMode = ref<'existing' | 'new'>('existing')
const bucket = ref('')
const bucketOptions = ref<string[]>([])
const refreshingBuckets = ref(false)
const authStatus = ref<S3AuthStatus>('idle')
const authError = ref('')
const prefix = ref(DEFAULT_S3_OBJECT_PREFIX)
const quota = ref(0)
const quotaUnit = ref<RepositoryQuotaUnit>('GB')
const enableQuotaAlert = ref(false)
const quotaAlertThreshold = ref(80)
let bucketSelectLoadAttemptAt = 0
let authValidationRevision = 0

/* ---------- computed ---------- */
const currentRegions = computed<RegionPreset[]>(() => {
  const provider = catalogProviders.value.find((item) => item.id === storagePlatform.value)
  return (provider?.regions || []).map((item) => ({
    key: item.id,
    label: item.display_name,
    endpoint: item.external_endpoint,
    externalEndpoint: item.external_endpoint,
    internalEndpoint: item.internal_endpoint,
    endpointType: 'external' as const,
    region: item.id,
    regionGroup: item.region_group,
    regionGroupLabel: item.region_group_en,
    s3UrlStyle: item.s3_url_style,
    useTls: item.use_tls,
  }))
})

function optionLabel(platform: { label?: string; labelKey?: string }) {
  return platform.label || (platform.labelKey ? t(platform.labelKey) : '')
}

const filteredPlatforms = computed(() => {
  const keyword = platformSearch.value.trim().toLowerCase()
  if (!keyword) return STORAGE_PLATFORMS.value
  return STORAGE_PLATFORMS.value.filter((platform) =>
    optionLabel(platform).toLowerCase().includes(keyword) || platform.value.toLowerCase().includes(keyword),
  )
})

const filteredRegions = computed(() => {
  const keyword = regionSearch.value.trim().toLowerCase()
  if (!keyword) return currentRegions.value
  return currentRegions.value.filter((preset) =>
    preset.label.toLowerCase().includes(keyword)
    || preset.region.toLowerCase().includes(keyword)
    || preset.regionGroup.toLowerCase().includes(keyword)
    || preset.regionGroupLabel.toLowerCase().includes(keyword),
  )
})

const groupedRegions = computed(() => {
  return groupS3RegionPresets(filteredRegions.value)
})

const platformLabel = computed(() => {
  const platform = STORAGE_PLATFORMS.value.find((item) => item.value === storagePlatform.value)
  return platform ? optionLabel(platform) : ''
})

function buildAutoRepoName(): string {
  return buildS3RepositoryName(platformLabel.value, bucket.value)
}

function syncAutoRepoName() {
  if (repoNameManuallyEdited.value) return
  syncingRepoName = true
  try {
    repoName.value = buildAutoRepoName()
  } catch (err) {
    console.error('Failed to sync auto repo name:', err)
  } finally {
    syncingRepoName = false
  }
}

function onRepoNameInput() {
  if (syncingRepoName) return
  repoNameManuallyEdited.value = true
}

const regionReadonly = computed(() => currentRegions.value.length > 0)

const endpointPlaceholder = computed(() =>
  storagePlatform.value === 'other'
    ? t('addS3Repo.phEndpointOther')
    : t('addS3Repo.phEndpoint'),
)

const s3UrlStyleLabel = computed(() => {
  if (s3UrlStyle.value === 'auto') return t('addS3Repo.s3UrlStyleAuto')
  if (s3UrlStyle.value === 'virtual_hosted') return t('addS3Repo.s3UrlStyleVirtualHosted')
  return t('addS3Repo.s3UrlStylePath')
})

const authStatusText = computed(() => {
  if (authStatus.value === 'validating') return t('addS3Repo.authValidating')
  if (authStatus.value === 'valid') return t('addS3Repo.authValid')
  if (authStatus.value === 'invalid') return authError.value || t('addS3Repo.authInvalid')
  return ''
})

const requiredAuthFieldsReady = computed(() =>
  !!storagePlatform.value
  && !!endpoint.value.trim()
  && !!accessKeyId.value.trim()
  && !!accessKeySecret.value.trim(),
)

const canSubmit = computed(() =>
  !busy.value
  && !refreshingBuckets.value
  && requiredAuthFieldsReady.value
  && !!repoName.value.trim()
  && !!bucket.value.trim()
  && (!enableQuotaAlert.value || validQuotaAlertThreshold.value),
)

const validQuotaAlertThreshold = computed(() => {
  const value = Number(quotaAlertThreshold.value || 0)
  return value >= 1 && value <= 100
})

const bucketSelectOptions = computed(() => {
  const selectedBucket = bucket.value.trim()
  if (!selectedBucket || bucketOptions.value.includes(selectedBucket)) {
    return bucketOptions.value
  }
  return [selectedBucket, ...bucketOptions.value]
})

const submitBlockReason = computed(() => {
  if (canSubmit.value || busy.value) return ''
  if (!requiredAuthFieldsReady.value) return t('addS3Repo.submitHintAuthFields')
  if (!repoName.value.trim()) return t('addS3Repo.submitHintRepoName')
  if (!bucket.value.trim()) return t('repositoriesPage.errBucket')
  if (enableQuotaAlert.value && !validQuotaAlertThreshold.value) return t('repositoriesPage.hintQuotaAlertThreshold')
  return ''
})

function extractBucketOptions(raw: unknown): string[] {
  const candidates: unknown[] = []
  if (Array.isArray(raw)) candidates.push(raw)
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>
    candidates.push(obj.buckets, obj.bucket_names)
    if (obj.data && typeof obj.data === 'object') {
      const data = obj.data as Record<string, unknown>
      candidates.push(data.buckets, data.bucket_names)
    }
  }
  const list = candidates.find(Array.isArray)
  if (!Array.isArray(list)) return []
  return list
    .map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const obj = item as Record<string, unknown>
        if (typeof obj.name === 'string') return obj.name
        if (typeof obj.bucket === 'string') return obj.bucket
      }
      return ''
    })
    .filter(Boolean)
}

function applyRegionPreset(key: string) {
  platformRegionKey.value = key
  const preset = currentRegions.value.find((r) => r.key === key)
  if (preset) {
    endpoint.value = normalizeS3EndpointInput(preset.externalEndpoint)
    region.value = preset.region
    s3UrlStyle.value = preset.s3UrlStyle
    useTls.value = preset.useTls
  }
}

function isPlatformEnabled(platform: StoragePlatformCapability): platform is StoragePlatform {
  const managed = catalogProviders.value.find((provider) => provider.id === platform)
  return managed ? managed.enabled : isS3ProviderEnabled(platform)
}

function onPlatformChange(p: StoragePlatformCapability) {
  if (!isPlatformEnabled(p)) {
    ElMessage.info({
      message: t('addS3Repo.platformComingSoon'),
      grouping: true,
    })
    return
  }
  const platformChanged = storagePlatform.value !== p
  storagePlatform.value = p
  if (platformChanged) {
    accessKeyId.value = ''
    accessKeySecret.value = ''
  }
  s3UrlStyle.value = defaultS3UrlStyle(p)
  useTls.value = true
  regionSearch.value = ''
  bucket.value = ''
  bucketOptions.value = []
  const presets = currentRegions.value
  if (presets.length > 0) {
    applyRegionPreset(presets[0].key)
  } else {
    platformRegionKey.value = undefined
    endpoint.value = ''
    region.value = ''
  }
}

async function loadProviderCatalog() {
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const catalog = await fetchStorageProviderCatalog()
    catalogProviders.value = catalog.providers.filter((provider) => provider.enabled)
  } catch (error) {
    catalogError.value = apiErrorMessage(error, t('addS3Repo.providerCatalogLoadFailed'))
  } finally {
    catalogLoading.value = false
  }
}

onMounted(loadProviderCatalog)

function resetAuthValidation() {
  authValidationRevision += 1
  authStatus.value = 'idle'
  authError.value = ''
  refreshingBuckets.value = false
  bucketOptions.value = []
  bucketSelectLoadAttemptAt = 0
}

function resetBucketSelectionForContext() {
  bucketOptions.value = []
  bucketSelectLoadAttemptAt = 0
  if (bucketMode.value === 'existing') bucket.value = ''
}

function setBucketMode(mode: 'existing' | 'new') {
  if (bucketMode.value === mode) return
  bucketMode.value = mode
  bucket.value = ''
}

function validateAuthFields(): boolean {
  if (!storagePlatform.value) {
    ElMessage.warning({ message: t('repositoriesPage.errPlatform'), grouping: true })
    return false
  }
  if (!endpoint.value.trim()) {
    ElMessage.warning({ message: t('addS3Repo.errEndpoint'), grouping: true })
    return false
  }
  if (!accessKeyId.value.trim()) {
    ElMessage.warning({ message: t('addS3Repo.errAccessKey'), grouping: true })
    return false
  }
  if (!accessKeySecret.value.trim()) {
    ElMessage.warning({ message: t('addS3Repo.errSecretKey'), grouping: true })
    return false
  }
  return true
}

function buildS3ValidationPayload(count: number) {
  return {
    platform: storagePlatform.value,
    endpoint: normalizeS3EndpointInput(endpoint.value),
    region: region.value.trim(),
    access_key_id: accessKeyId.value.trim(),
    secret_access_key: accessKeySecret.value,
    s3_url_style: s3UrlStyle.value,
    use_tls: useTls.value,
    count,
  }
}

async function validateS3Auth(
  options: {
    clearBucket?: boolean
    showSuccess?: boolean
    bucketCount?: number
    updateBuckets?: boolean
  } = {},
): Promise<boolean> {
  if (!validateAuthFields()) return false
  if (refreshingBuckets.value) return authStatus.value === 'valid'
  const {
    clearBucket = false,
    showSuccess = true,
    bucketCount = 3,
    updateBuckets = false,
  } = options
  const validationRevision = authValidationRevision
  refreshingBuckets.value = true
  authStatus.value = 'validating'
  authError.value = ''
  if (updateBuckets) bucketOptions.value = []
  if (clearBucket) bucket.value = ''
  try {
    const raw = await api<unknown>(S3_AUTH_VALIDATE_ENDPOINT, {
      method: 'POST',
      body: JSON.stringify(buildS3ValidationPayload(bucketCount)),
    })
    if (validationRevision !== authValidationRevision) return false
    if (updateBuckets) bucketOptions.value = extractBucketOptions(raw)
    authStatus.value = 'valid'
    if (showSuccess) ElMessage.success({ message: t('addS3Repo.authValid'), grouping: true })
    return true
  } catch (err) {
    if (validationRevision !== authValidationRevision) return false
    authStatus.value = 'invalid'
    authError.value = apiErrorMessage(err, t('addS3Repo.authInvalid'))
    if (updateBuckets) bucketOptions.value = []
    return false
  } finally {
    if (validationRevision === authValidationRevision) refreshingBuckets.value = false
  }
}

function refreshBuckets() {
  void validateS3Auth({ clearBucket: false, bucketCount: 1000, updateBuckets: true })
}

function loadBucketsForSelect() {
  if (bucketMode.value !== 'existing') return
  if (refreshingBuckets.value) return
  if (authStatus.value === 'valid' && bucketOptions.value.length > 0) return
  const now = Date.now()
  if (now - bucketSelectLoadAttemptAt < 500) return
  bucketSelectLoadAttemptAt = now
  void validateS3Auth({
    clearBucket: false,
    showSuccess: false,
    bucketCount: 1000,
    updateBuckets: true,
  })
}

watch(
  [storagePlatform, platformRegionKey, endpoint, region],
  resetBucketSelectionForContext,
)

watch(
  [storagePlatform, platformRegionKey, endpoint, region, accessKeyId, accessKeySecret, s3UrlStyle, useTls],
  resetAuthValidation,
)

watch([storagePlatform, platformLabel, bucket], syncAutoRepoName, { immediate: true })

function validateForm(): boolean {
  const bucketNameError = bucketMode.value === 'new' && bucket.value.trim()
    ? s3BucketNameError(normalizeS3Platform(storagePlatform.value), bucket.value)
    : null
  const bucketNameMessage = bucketNameError
    ? t(`addS3Repo.bucketNameErrors.${bucketNameError}`)
    : t('repositoriesPage.errBucket')
  return validateInline([
    { field: 'platform', message: t('repositoriesPage.errPlatform'), valid: !!storagePlatform.value },
    { field: 'endpoint', message: t('addS3Repo.errEndpoint'), valid: !!endpoint.value.trim() },
    { field: 'accessKey', message: t('addS3Repo.errAccessKey'), valid: !!accessKeyId.value.trim() },
    { field: 'secretKey', message: t('addS3Repo.errSecretKey'), valid: !!accessKeySecret.value.trim() },
    { field: 'repoName', message: t('repositoriesPage.errName'), valid: !!repoName.value.trim() },
    {
      field: 'bucket',
      message: bucketNameMessage,
      valid: !!bucket.value.trim() && !bucketNameError,
    },
    { field: 'quotaThreshold', message: t('repositoriesPage.hintQuotaAlertThreshold'), valid: !enableQuotaAlert.value || validQuotaAlertThreshold.value },
  ])
}

async function onSubmit() {
  if (!validateForm()) return
  busy.value = true
  try {
    if (!props.embedded && authStatus.value !== 'valid') {
      const authValid = await validateS3Auth({ clearBucket: false, showSuccess: false })
      if (!authValid) return
    }
    const created = await createStorageRepository(buildCreatePayload())
    const accepted = String(created.status || '').toLowerCase() === 'creating'
    ElMessage.success({
      message: t(accepted ? 'repositoriesPage.msgCreateAccepted' : 'repositoriesPage.msgCreated'),
      grouping: true,
    })
    if (props.embedded) {
      emit('created', created)
    } else {
      router.push({
        path: '/node/repositories',
        query: {
          tab: 's3',
          ...(accepted && created.active_create_task?.task_uuid
            ? {
                pending_create_id: String(created.id),
                pending_create_task: created.active_create_task.task_uuid,
              }
            : {}),
        },
      })
    }
  } catch (err) {
    ElMessage.error({ message: storageRepositoryCreateErrorMessage(err, t), grouping: true })
  } finally {
    busy.value = false
  }
}

function normalizeS3Platform(platform: StoragePlatform | undefined): string {
  return normalizeS3StoragePlatform(platform)
}

function buildCreatePayload() {
  return {
    name: repoName.value.trim(),
    repo_type: 's3',
    s3_platform: normalizeS3Platform(storagePlatform.value),
    s3_bucket: bucket.value.trim(),
    s3_bucket_mode: storageRepositoryS3BucketMode(bucketMode.value),
    config: {
      region: region.value.trim() || undefined,
      endpoint: normalizeS3EndpointInput(endpoint.value) || undefined,
      prefix: normalizeS3ObjectPrefix(prefix.value) || undefined,
      access_key_id: accessKeyId.value.trim(),
      secret_access_key: accessKeySecret.value,
      s3_url_style: s3UrlStyle.value,
      use_tls: useTls.value,
      quota_gb: repositoryQuotaToGb(quota.value, quotaUnit.value),
      quota_unit: quotaUnit.value,
      quota_alert_enabled: enableQuotaAlert.value,
      quota_alert_threshold: enableQuotaAlert.value ? Number(quotaAlertThreshold.value || 0) : 0,
    },
  }
}

watch(enableQuotaAlert, (enabled) => {
  if (enabled && !quotaAlertThreshold.value) quotaAlertThreshold.value = 80
})

function maskSecretAccessKey(secret: string): string {
  return secret.trim() ? '******' : ''
}

function handleBack() {
  if (props.embedded) {
    emit('cancel')
    return
  }
  router.push({ path: '/node/repositories', query: { tab: 's3' } })
}
</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen"
    :class="{ 'resource-add-fullscreen--embedded': embedded }"
  >
    <div class="fullscreen-form-page add-s3-page">
      <!-- Header -->
      <div
        v-if="!embedded"
        class="fullscreen-form-header"
      >
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
            {{ t('addS3Repo.pageTitle') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('addS3Repo.pageDesc') }}
          </p>
        </div>
      </div>

      <div class="fullscreen-form-layout">
        <!-- Main Form Area -->
        <div class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <div class="fullscreen-form-section__head">
                  <h3 class="fullscreen-form-section__title">
                    <span class="fullscreen-form-section__indicator" />
                    {{ t('addS3Repo.fieldPlatform') }}
                  </h3>
                  <div class="add-s3-search">
                    <ElInput
                      v-model="platformSearch"
                      class="add-s3-search__input"
                      :placeholder="t('addS3Repo.phSearchPlatform')"
                      clearable
                    >
                      <template #prefix>
                        <Search :size="14" />
                      </template>
                    </ElInput>
                  </div>
                </div>
                <div
                  v-if="catalogError"
                  class="add-s3-empty-state"
                >
                  {{ catalogError }}
                  <ElButton
                    link
                    type="primary"
                    @click="loadProviderCatalog"
                  >
                    {{ t('common.retry') }}
                  </ElButton>
                </div>
                <div
                  v-loading="catalogLoading"
                  class="add-s3-platform-grid add-s3-platform-grid--331"
                >
                  <button
                    v-for="p in filteredPlatforms"
                    :key="p.value"
                    class="add-s3-platform-btn"
                    :class="{
                      'add-s3-platform-btn--active': storagePlatform === p.value,
                      'add-s3-platform-btn--disabled': !isPlatformEnabled(p.value),
                    }"
                    :aria-disabled="!isPlatformEnabled(p.value)"
                    :title="!isPlatformEnabled(p.value) ? t('addS3Repo.platformComingSoon') : undefined"
                    @click="onPlatformChange(p.value)"
                  >
                    <div
                      v-if="storagePlatform === p.value"
                      class="add-s3-platform-btn__glow"
                    />
                    <span class="add-s3-platform-btn__icon">
                      <S3PlatformBrandIcon
                        :platform="p.value"
                        :size="18"
                        :alt="optionLabel(p)"
                        icon-class="add-s3-platform-btn__icon-img"
                        lucide-class="add-s3-platform-btn__icon-lucide"
                      />
                    </span>
                    <span class="add-s3-platform-btn__label">{{ optionLabel(p) }}</span>
                  </button>
                  <div
                    v-if="filteredPlatforms.length === 0"
                    class="add-s3-empty-state"
                  >
                    {{ t('addS3Repo.emptyPlatforms') }}
                  </div>
                </div>
                <p
                  v-if="errors.platform"
                  class="el-form-item__error"
                >
                  {{ errors.platform }}
                </p>

                <!-- Region Selection -->
                <template v-if="currentRegions.length > 0">
                  <div class="add-s3-region-head">
                    <h4 class="fullscreen-form-section__subtitle">
                      {{ t('addS3Repo.fieldRegion') }} <span class="fullscreen-form-field__required">*</span>
                    </h4>
                    <div class="add-s3-search">
                      <ElInput
                        v-model="regionSearch"
                        class="add-s3-search__input"
                        :placeholder="t('addS3Repo.phSearchRegion')"
                        clearable
                      >
                        <template #prefix>
                          <Search :size="14" />
                        </template>
                      </ElInput>
                    </div>
                  </div>
                  <div class="add-s3-region-groups add-s3-region-grid--scroll">
                    <section
                      v-for="group in groupedRegions"
                      :key="group.key"
                      class="add-s3-region-group"
                    >
                      <h5 class="add-s3-region-group__title">
                        {{ group.label }}
                      </h5>
                      <div class="add-s3-region-grid">
                        <button
                          v-for="r in group.regions"
                          :key="r.key"
                          class="add-s3-region-btn"
                          :class="{ 'add-s3-region-btn--active': platformRegionKey === r.key }"
                          @click="applyRegionPreset(r.key)"
                        >
                          <span class="add-s3-region-btn__label">{{ r.label }}</span>
                          <span class="add-s3-region-btn__code">{{ r.region }}</span>
                        </button>
                      </div>
                    </section>
                    <div
                      v-if="filteredRegions.length === 0"
                      class="add-s3-empty-state"
                    >
                      {{ t('addS3Repo.emptyRegions') }}
                    </div>
                  </div>
                </template>
              </section>
            </div>

            <div class="fullscreen-form-card">
              <section class="fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('addS3Repo.connectionTitle') }}
                </h3>

                <div class="fullscreen-form-grid">
                  <!-- Endpoint -->
                  <div
                    data-validation-field="endpoint"
                    class="fullscreen-form-field"
                  >
                    <label class="fullscreen-form-field__label">
                      {{ t('addS3Repo.fieldEndpoint') }} <span class="fullscreen-form-field__required">*</span>
                    </label>
                    <ElInput
                      v-model="endpoint"
                      class="add-s3-element-field"
                      :placeholder="endpointPlaceholder"
                      :disabled="!!platformRegionKey"
                      @blur="endpoint = normalizeS3EndpointInput(endpoint)"
                      @input="clearFieldError('endpoint')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintEndpoint') }}
                    </p>
                    <p
                      v-if="errors.endpoint"
                      class="el-form-item__error"
                    >
                      {{ errors.endpoint }}
                    </p>
                  </div>

                  <!-- Region -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label">
                      {{ t('addS3Repo.fieldRegion') }}
                    </label>
                    <ElInput
                      v-model="region"
                      class="add-s3-element-field"
                      :placeholder="t('addS3Repo.phRegion')"
                      :disabled="regionReadonly"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintRegion') }}
                    </p>
                  </div>

                  <!-- Access Key -->
                  <div
                    data-validation-field="accessKey"
                    class="fullscreen-form-field"
                  >
                    <label class="fullscreen-form-field__label">
                      {{ t('addS3Repo.fieldAccessKey') }} <span class="fullscreen-form-field__required">*</span>
                    </label>
                    <ElInput
                      v-model="accessKeyId"
                      class="add-s3-element-field"
                      :placeholder="t('addS3Repo.phAccessKey')"
                      @input="clearFieldError('accessKey')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintAccessKey') }}
                    </p>
                    <p
                      v-if="errors.accessKey"
                      class="el-form-item__error"
                    >
                      {{ errors.accessKey }}
                    </p>
                  </div>

                  <!-- Secret Key -->
                  <div
                    data-validation-field="secretKey"
                    class="fullscreen-form-field"
                  >
                    <label class="fullscreen-form-field__label">
                      {{ t('addS3Repo.fieldSecretKey') }} <span class="fullscreen-form-field__required">*</span>
                    </label>
                    <ElInput
                      v-model="accessKeySecret"
                      type="password"
                      show-password
                      class="add-s3-element-field"
                      :placeholder="t('addS3Repo.phSecretKey')"
                      @input="clearFieldError('secretKey')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintSecretKey') }}
                    </p>
                    <p
                      v-if="errors.secretKey"
                      class="el-form-item__error"
                    >
                      {{ errors.secretKey }}
                    </p>
                  </div>

                  <!-- Path Style -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label">
                      {{ t('addS3Repo.fieldS3UrlStyle') }} <span class="fullscreen-form-field__required">*</span>
                    </label>
                    <ElSelect
                      v-model="s3UrlStyle"
                      class="add-s3-element-field"
                    >
                      <ElOption
                        :label="t('addS3Repo.s3UrlStyleAuto')"
                        value="auto"
                      />
                      <ElOption
                        :label="t('addS3Repo.s3UrlStylePath')"
                        value="path"
                      />
                      <ElOption
                        :label="t('addS3Repo.s3UrlStyleVirtualHosted')"
                        value="virtual_hosted"
                      />
                    </ElSelect>
                    <p class="fullscreen-form-field__hint">
                      {{ t('addS3Repo.hintS3UrlStyle') }}
                    </p>
                  </div>

                  <!-- TLS Toggle -->
                  <div class="fullscreen-form-field">
                    <label class="fullscreen-form-field__label fullscreen-form-field__label--toggle">
                      {{ t('addS3Repo.fieldUseTls') }} <span class="fullscreen-form-field__required">*</span>
                    </label>
                    <div class="add-s3-toggle">
                      <ElSwitch v-model="useTls" />
                      <span class="fullscreen-form-field__hint add-s3-toggle__label">{{ useTls ? t('addS3Repo.tlsOnHint') : t('addS3Repo.tlsOffHint') }}</span>
                    </div>
                  </div>
                </div>

                <div
                  v-if="authStatus !== 'idle'"
                  class="add-s3-auth-check"
                >
                  <p
                    class="add-s3-auth-status"
                    :class="`add-s3-auth-status--${authStatus}`"
                  >
                    <span class="add-s3-auth-status__dot" />
                    {{ authStatusText }}
                  </p>
                </div>
              </section>
            </div>

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
                      data-validation-field="repoName"
                      class="fullscreen-form-field"
                    >
                      <label class="fullscreen-form-field__label">
                        {{ t('addS3Repo.fieldRepoName') }} <span class="fullscreen-form-field__required">*</span>
                      </label>
                      <ElInput
                        v-model="repoName"
                        class="add-s3-element-field add-s3-repo-primary-input"
                        :placeholder="t('addS3Repo.phRepoName')"
                        @input="onRepoNameInput(); clearFieldError('repoName')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('addS3Repo.hintRepoName') }}
                      </p>
                      <p
                        v-if="errors.repoName"
                        class="el-form-item__error"
                      >
                        {{ errors.repoName }}
                      </p>
                    </div>

                    <!-- Bucket -->
                    <div
                      data-validation-field="bucket"
                      class="fullscreen-form-field"
                    >
                      <label class="fullscreen-form-field__label">
                        {{ t('addS3Repo.fieldBucket') }} <span class="fullscreen-form-field__required">*</span>
                      </label>
                      <ElRadioGroup
                        :model-value="bucketMode"
                        class="source-radio-row hfl-segment-radio-row add-s3-bucket-segment"
                        @update:model-value="(mode) => setBucketMode(mode as 'existing' | 'new')"
                      >
                        <ElRadio
                          value="existing"
                          border
                          class="source-radio-card !mr-0"
                        >
                          {{ t('addS3Repo.fieldBucketExisting') }}
                        </ElRadio>
                        <ElRadio
                          value="new"
                          border
                          class="source-radio-card !mr-0"
                        >
                          {{ t('addS3Repo.fieldBucketNew') }}
                        </ElRadio>
                      </ElRadioGroup>
                      <div
                        v-if="bucketMode === 'existing'"
                        class="add-s3-bucket-select-row"
                      >
                        <ElSelect
                          v-model="bucket"
                          class="add-s3-bucket-select"
                          filterable
                          clearable
                          :loading="refreshingBuckets"
                          :placeholder="refreshingBuckets ? t('addS3Repo.btnValidatingAuth') : t('addS3Repo.phBucketSelect')"
                          @focus="loadBucketsForSelect"
                          @visible-change="(open) => open && loadBucketsForSelect()"
                          @change="clearFieldError('bucket')"
                          @clear="bucket = ''"
                        >
                          <ElOption
                            v-for="item in bucketSelectOptions"
                            :key="item"
                            :label="item"
                            :value="item"
                          />
                        </ElSelect>
                        <button
                          class="add-s3-icon-btn"
                          type="button"
                          :title="t('common.refresh')"
                          :disabled="refreshingBuckets"
                          @click="refreshBuckets"
                        >
                          <RefreshCw
                            class="add-s3-icon-btn__icon"
                            :class="{ 'add-s3-icon-btn__icon--spinning': refreshingBuckets }"
                            :size="16"
                          />
                        </button>
                      </div>
                      <ElInput
                        v-else
                        v-model="bucket"
                        class="add-s3-element-field add-s3-repo-primary-input"
                        :placeholder="t('addS3Repo.phBucketNew')"
                        @input="clearFieldError('bucket')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('addS3Repo.hintBucket') }}
                      </p>
                      <p
                        v-if="errors.bucket"
                        class="el-form-item__error"
                      >
                        {{ errors.bucket }}
                      </p>
                    </div>
                    <!-- Prefix -->
                    <div
                      data-validation-field="prefix"
                      class="fullscreen-form-field"
                    >
                      <label class="fullscreen-form-field__label">
                        {{ t('addS3Repo.fieldPrefix') }}
                      </label>
                      <ElInput
                        v-model="prefix"
                        class="add-s3-element-field add-s3-repo-primary-input"
                        :placeholder="t('addS3Repo.phPrefix')"
                        @blur="prefix = normalizeS3ObjectPrefix(prefix)"
                        @input="clearFieldError('prefix')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('addS3Repo.hintPrefix') }}
                      </p>
                    </div>
                  </div>

                  <!-- Quota -->
                  <div class="repository-quota-pair">
                    <div class="fullscreen-form-field repository-quota-field">
                      <label class="fullscreen-form-field__label repository-quota-head">{{ t('addS3Repo.fieldQuota') }}</label>
                      <div class="repository-quota-control">
                        <div class="repository-quota-number repository-quota-input repository-quota-split-input repository-quota-split-input--add">
                          <ElInputNumber
                            v-model="quota"
                            class="repository-quota-number__input"
                            :placeholder="t('addS3Repo.phQuota')"
                            :min="0"
                            :precision="0"
                            :step="1"
                            controls-position="right"
                          />
                          <ElSelect
                            v-model="quotaUnit"
                            class="repository-quota-number__unit"
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
                      class="fullscreen-form-field repository-quota-field repository-quota-field--monitoring"
                    >
                      <div class="fullscreen-form-field__label repository-quota-head repository-quota-title-row">
                        <ElCheckbox v-model="enableQuotaAlert">
                          {{ t('repositoriesPage.fieldQuotaAlert') }}
                        </ElCheckbox>
                      </div>
                      <div class="repository-quota-control">
                        <div class="repository-quota-number repository-quota-input">
                          <ElInputNumber
                            v-model="quotaAlertThreshold"
                            class="repository-quota-number__input"
                            :min="1"
                            :max="100"
                            controls-position="right"
                            @change="clearFieldError('quotaThreshold')"
                          />
                          <div class="repository-quota-number__suffix">
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

          <!-- Footer Actions -->
          <div class="fullscreen-form-footer add-s3-footer">
            <p
              v-if="submitBlockReason"
              class="form-submit-hint"
            >
              {{ submitBlockReason }}
            </p>
            <ElButton @click="handleBack">
              {{ t('repositoriesPage.btnCancel') }}
            </ElButton>
            <ElButton
              type="primary"
              :loading="busy"
              :disabled="!canSubmit"
              @click="onSubmit"
            >
              {{ t('addS3Repo.btnCreateInit') }}
            </ElButton>
          </div>
        </div>

        <aside
          v-if="!embedded"
          class="fullscreen-form-sidebar add-form-preview-sidebar"
        >
          <div class="add-form-preview-card">
            <!-- Preview Header -->
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon">
                <S3PlatformBrandIcon
                  :platform="storagePlatform"
                  :size="28"
                  :alt="platformLabel || t('addS3Repo.previewSelectPlatform')"
                  icon-class="add-form-preview-header__icon-img"
                  lucide-class="add-form-preview-header__icon-lucide"
                />
              </div>
              <div class="add-form-preview-header__info">
                <h4 class="add-form-preview-header__name">
                  {{ repoName || t('addS3Repo.previewUnnamed') }}
                </h4>
                <p class="add-form-preview-header__type">
                  {{ platformLabel || t('addS3Repo.previewSelectPlatform') }}
                </p>
              </div>
            </div>

            <!-- Preview Body -->
            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('addS3Repo.previewConnectionAuth') }}
                </h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldEndpoint') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !endpoint }"
                  >
                    {{ s3EndpointDisplay(endpoint) }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldRegion') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !region }"
                  >
                    {{ region || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldAccessKey') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{
                      'add-form-preview-row__value--empty': !accessKeyId.trim(),
                      'add-form-preview-row__value--mono': !!accessKeyId.trim(),
                    }"
                  >
                    {{ accessKeyId.trim() || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldSecretKey') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{
                      'add-form-preview-row__value--empty': !accessKeySecret.trim(),
                      'add-form-preview-row__value--mono': !!accessKeySecret.trim(),
                    }"
                  >
                    {{ accessKeySecret.trim() ? maskSecretAccessKey(accessKeySecret) : '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldS3UrlStyle') }}</span>
                  <span class="add-form-preview-row__value">{{ s3UrlStyleLabel }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldUseTls') }}</span>
                  <span
                    class="add-form-preview-row__value"
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
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldBucket') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !bucket }"
                  >
                    {{ bucket || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldPrefix') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !prefix }"
                  >
                    {{ prefix || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addS3Repo.fieldQuota') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--highlight': quota > 0 }"
                  >
                    {{ quota > 0 ? `${quota} ${quotaUnit}` : t('addS3Repo.previewUnlimited') }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldQuotaAlert') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="enableQuotaAlert ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'"
                  >
                    <span
                      v-if="enableQuotaAlert"
                      class="add-form-preview-row__dot add-form-preview-row__dot--green"
                    />
                    <template v-if="enableQuotaAlert">
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

<style scoped>
/* ============================================
   Step Indicator
   ============================================ */
.add-s3-steps {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
  padding: 16px 0;
}

.add-s3-steps__item {
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.5;
  transition: opacity 0.2s ease;
}

.add-s3-steps__item--active,
.add-s3-steps__item--done {
  opacity: 1;
}

.add-s3-steps__num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  background: rgb(248 250 252);
  border: 1px solid rgba(203, 213, 225, 0.95);
  color: rgb(100 116 139);
  transition: all 0.2s ease;
}

.add-s3-steps__item--active .add-s3-steps__num,
.add-s3-steps__item--done .add-s3-steps__num {
  background: linear-gradient(180deg, rgb(37 99 235) 0%, rgb(29 78 216) 100%);
  border-color: rgb(29 78 216);
  color: #fff;
}

.add-s3-steps__label {
  font-size: 14px;
  font-weight: 500;
  color: rgb(30 41 59);
}

.add-s3-steps__connector {
  flex: 1;
  max-width: 60px;
  height: 2px;
  background: rgba(203, 213, 225, 0.9);
  border-radius: 1px;
  transition: background 0.3s ease;
}

.add-s3-steps__connector--on {
  background: rgb(37 99 235);
}

/* ============================================
   Background Effects
   ============================================ */
.add-s3-bg {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.add-s3-bg__glow {
  position: absolute;
  width: 600px;
  height: 400px;
  top: -100px;
  right: 10%;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.15;
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-hover));
}

/* ============================================
   Region Selection
   ============================================ */
.add-s3-region-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.add-s3-region-groups {
  display: grid;
  gap: 10px;
}

.add-s3-region-group {
  display: grid;
  gap: 6px;
}

.add-s3-region-group__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--color-text-secondary, #b7bdc7);
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.add-s3-region-group__title::after {
  height: 1px;
  flex: 1;
  background: color-mix(in srgb, currentColor 18%, transparent);
  content: '';
}

.add-s3-region-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
  min-height: 38px;
  padding: 5px 10px;
  background: #1b202a;
  border: 1px solid #3b4658;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.add-s3-region-btn:hover {
  border-color: #5a6f8f;
  background: #202736;
}

.add-s3-region-btn--active {
  border-color: var(--color-primary);
  background: rgba(69, 125, 176, 0.18);
  box-shadow: inset 0 0 0 1px rgba(69, 125, 176, 0.18);
}

.add-s3-region-btn__label {
  width: 100%;
  overflow: hidden;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-title, #E2E2E2);
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.add-s3-region-btn--active .add-s3-region-btn__label {
  color: var(--color-primary);
}

.add-s3-region-btn__code {
  width: 100%;
  overflow: hidden;
  font-size: 10px;
  color: var(--el-text-color-secondary, #B7BDC7);
  margin-top: 0;
  font-family: var(--font-mono, monospace);
  line-height: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .add-s3-page {
    padding-bottom: calc(128px + var(--app-safe-bottom));
  }

  .add-s3-footer {
    min-height: calc(104px + var(--app-safe-bottom));
    flex-wrap: wrap;
    align-content: center;
    padding-top: 10px;
    padding-bottom: calc(10px + var(--app-safe-bottom));
  }

  .add-s3-footer .form-submit-hint {
    flex: 1 0 100%;
    line-height: 1.4;
  }

  .add-s3-footer .el-button {
    white-space: nowrap;
  }

  .add-s3-footer .el-button:first-of-type {
    margin-left: auto;
  }

  .add-s3-auth-check {
    align-items: stretch;
    flex-direction: column;
  }

  .form-submit-hint {
    text-align: left;
  }

  .repository-quota-pair {
    grid-template-columns: 1fr;
  }

}

.add-s3-repo-primary-fields {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.add-s3-repo-primary-input {
  width: 100%;
}

.add-s3-bucket-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.add-s3-bucket-select {
  flex: 1 1 auto;
  min-width: 0;
}

.add-s3-element-field,
.add-s3-number-field {
  width: 100%;
}

.add-s3-search :deep(.add-s3-search__input.el-input) {
  height: auto;
  padding: 0;
  border: 0;
  background: transparent;
}

.add-s3-search :deep(.el-input__wrapper) {
  min-height: 32px;
  border-radius: 6px;
}

.add-s3-element-field :deep(.el-input__wrapper),
.add-s3-element-field :deep(.el-select__wrapper),
.add-s3-bucket-select :deep(.el-select__wrapper),
.add-s3-number-field :deep(.el-input__wrapper) {
  min-height: 40px;
  border-radius: 6px;
}

.add-s3-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 34px;
  flex: 0 0 40px;
  padding: 0;
  background: #1b202a;
  border: 1px solid #3b4658;
  border-radius: 6px;
  color: var(--color-text-secondary, #A3A6AD);
  cursor: pointer;
  transition: all 0.15s ease;
}

.add-s3-icon-btn:hover:not(:disabled) {
  border-color: #5a6f8f;
  color: #ffffff;
  background: #202736;
}

.add-s3-icon-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.add-s3-icon-btn__icon {
  flex-shrink: 0;
}

.add-s3-icon-btn__icon--spinning {
  animation: spin 0.8s linear infinite;
}

/* ============================================
   Toggle Switch
   ============================================ */
.add-s3-toggle {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.add-s3-toggle__label {
  display: inline-flex;
  align-items: center;
}

/* ============================================
   Authentication Check
   ============================================ */
.add-s3-auth-check {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border, #2A2B35);
}

.add-s3-auth-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  min-width: 0;
  flex: 1 1 auto;
  font-size: 13px;
  color: var(--color-text-secondary, #A3A6AD);
}

.add-s3-auth-status__dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 999px;
  background: var(--color-text-tertiary, #70727B);
}

.add-s3-auth-status--valid {
  color: #4ADE80;
}

.add-s3-auth-status--valid .add-s3-auth-status__dot {
  background: #4ADE80;
  box-shadow: 0 0 6px rgba(74, 222, 128, 0.65);
}

.add-s3-auth-status--invalid {
  color: #F87171;
}

.add-s3-auth-status--invalid .add-s3-auth-status__dot {
  background: #F87171;
  box-shadow: 0 0 6px rgba(248, 113, 113, 0.55);
}

.add-s3-auth-status--validating {
  color: #93C5FD;
}

.add-s3-auth-status--validating .add-s3-auth-status__dot {
  background: #93C5FD;
  animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

/* ============================================
   Preview (page-specific dark theme overrides)
   ============================================ */
.add-form-preview-card {
  background: var(--color-card-bg, #1C1D24);
  border: 1px solid var(--color-border, #2A2B35);
  border-radius: 12px;
  overflow: hidden;
}

.add-form-preview-header {
  position: relative;
  padding: 24px;
  background: linear-gradient(135deg, var(--el-fill-color-light, #252630), var(--el-fill-color-lighter, #2A2B35));
  border-bottom: 1px solid var(--color-border, #2A2B35);
  overflow: hidden;
}

.add-form-preview-header__glow {
  position: absolute;
  top: -40px;
  right: -40px;
  width: 120px;
  height: 120px;
  background: radial-gradient(circle, rgba(69, 125, 176, 0.2), transparent 70%);
  border-radius: 50%;
  filter: blur(20px);
}

.add-form-preview-header__icon {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  background: var(--el-fill-color-blank, #16161D);
  border: 1px solid var(--color-border, #3A3B45);
  border-radius: 12px;
  margin-bottom: 14px;
}

.add-form-preview-header__icon-img {
  width: 28px;
  height: 28px;
  object-fit: contain;
  display: block;
}

.add-form-preview-header__icon-lucide {
  color: #4f46e5;
}

.add-form-preview-header__info {
  position: relative;
  z-index: 1;
}

.add-form-preview-header__name {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-title, #E2E2E2);
  margin: 0 0 4px;
  line-height: 1.3;
}

.add-form-preview-header__type {
  font-size: 13px;
  color: var(--el-text-color-regular, #C7CCD4);
  margin: 0;
}

.add-form-preview-body {
  padding: 20px;
}

.add-form-preview-section {
  padding-bottom: 16px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--color-border, #2A2B35);
}

.add-form-preview-section:last-child {
  padding-bottom: 0;
  margin-bottom: 0;
  border-bottom: none;
}

.add-form-preview-section__title {
  font-size: 11px;
  font-weight: 600;
  text-transform: none;
  letter-spacing: 0;
  color: var(--color-text-title, #1d2129);
  margin: 0 0 12px;
}

.add-form-preview-row__label {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-regular, #C7CCD4);
  flex-shrink: 0;
}

.add-form-preview-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}

.add-form-preview-row:last-child {
  margin-bottom: 0;
}

.add-form-preview-row__value {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-title, #E2E2E2);
  text-align: right;
  word-break: break-all;
}

.add-form-preview-row__value--mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.add-form-preview-row__value--highlight,
.add-form-preview-row__value--primary {
  color: var(--color-primary);
}

.add-form-preview-row__value--primary {
  padding: 2px 8px;
  background: rgba(69, 125, 176, 0.1);
  border-radius: 4px;
}

.add-form-preview-row__value--success {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-success-text);
}

.add-form-preview-row__value--muted {
  color: var(--color-text-tertiary, #70727B);
}

.add-form-preview-row__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.add-form-preview-row__dot--green {
  background: #4ADE80;
  box-shadow: 0 0 6px #4ADE80;
}

.add-form-preview-row__shield {
  color: #4ADE80;
}

/* ============================================
   Light Theme Adjustments
   ============================================ */
:root:not([data-theme="dark"]) .add-s3-region-btn {
  background: #f8fbff;
  border-color: #c8d3e0;
}

:root:not([data-theme="dark"]) .add-s3-region-btn__label {
  color: var(--color-text-title, #303133);
}

:root:not([data-theme="dark"]) .add-s3-region-btn--active {
  background: rgba(69, 125, 176, 0.14);
  border-color: var(--color-primary, #457AB0);
}

:root:not([data-theme="dark"]) .add-s3-region-btn--active .add-s3-region-btn__label {
  color: var(--color-primary, #457AB0);
}

:root:not([data-theme="dark"]) .add-s3-icon-btn {
  background: #f8fbff;
  border-color: #c8d3e0;
  color: #64748b;
}

:root:not([data-theme="dark"]) .add-s3-icon-btn:hover:not(:disabled) {
  border-color: #7a99bc;
  background: #f1f6fc;
  color: var(--color-primary, #457AB0);
}

:root:not([data-theme="dark"]) .add-s3-steps__num {
  background: rgb(248 250 252);
  border-color: rgba(203, 213, 225, 0.95);
  color: rgb(100 116 139);
}

:root:not([data-theme="dark"]) .add-s3-steps__item--active .add-s3-steps__num,
:root:not([data-theme="dark"]) .add-s3-steps__item--done .add-s3-steps__num {
  background: linear-gradient(180deg, rgb(37 99 235) 0%, rgb(29 78 216) 100%);
  border-color: rgb(29 78 216);
  color: #fff;
}

:root:not([data-theme="dark"]) .add-s3-steps__label {
  color: rgb(30 41 59);
}

:root:not([data-theme="dark"]) .add-s3-steps__connector {
  background: rgba(203, 213, 225, 0.9);
}

:root:not([data-theme="dark"]) .add-s3-steps__connector--on {
  background: rgb(37 99 235);
}

:root:not([data-theme="dark"]) .add-form-preview-card {
  background: #fff;
  border-color: var(--color-border-light, #e4e7ed);
}

:root:not([data-theme="dark"]) .add-form-preview-header {
  background: linear-gradient(135deg, #f5f7fa, #ebeef5);
  border-color: var(--color-border-light, #e4e7ed);
}

:root:not([data-theme="dark"]) .add-form-preview-header__glow {
  background: radial-gradient(circle, rgba(69, 125, 176, 0.15), transparent 70%);
}

:root:not([data-theme="dark"]) .add-form-preview-header__icon {
  background: #fff;
  border-color: var(--color-border-light, #e4e7ed);
}

:root:not([data-theme="dark"]) .add-form-preview-header__name {
  color: var(--color-text-title, #303133);
}

:root:not([data-theme="dark"]) .add-form-preview-header__type {
  color: var(--color-text-secondary, #606266);
}

:root:not([data-theme="dark"]) .add-form-preview-section__title {
  color: var(--color-text-tertiary, #909399);
}

:root:not([data-theme="dark"]) .add-form-preview-row__label {
  color: var(--color-text-secondary, #606266);
}

:root:not([data-theme="dark"]) .add-form-preview-row__value {
  color: var(--color-text-title, #303133);
}

:root:not([data-theme="dark"]) .add-form-preview-row__value--primary {
  background: rgba(69, 125, 176, 0.1);
  color: var(--color-primary, #457AB0);
}

:root:not([data-theme="dark"]) .add-form-preview-row__value--success {
  color: var(--color-success-text);
}

:root:not([data-theme="dark"]) .add-form-preview-row__value--muted {
  color: var(--color-text-tertiary, #909399);
}

:root:not([data-theme="dark"]) .add-form-preview-row__dot--green {
  background: #4ade80;
  box-shadow: 0 0 6px rgba(74, 222, 128, 0.45);
}

:root:not([data-theme="dark"]) .add-form-preview-row__shield {
  color: #4ade80;
}

:root:not([data-theme="dark"]) .add-s3-bg__glow {
  opacity: 0.08;
}

/* ============================================
   Screen Reader Only
   ============================================ */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
