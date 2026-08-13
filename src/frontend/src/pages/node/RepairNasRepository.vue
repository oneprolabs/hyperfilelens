<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox, type InputInstance } from 'element-plus'
import { ArrowLeft, Plus, RefreshCw, ShieldCheck, Wrench } from 'lucide-vue-next'
import { nasMountProtocolIcon } from '../../lib/resourceIcons'
import { apiErrorMessage } from '../../lib/api'
import {
  getStorageRepository,
  listStorageRepositoryAssociatedSources,
  repairStorageRepository,
  type StorageRepository,
} from '../../lib/storageRepositoryApi'
import { listAllNodes } from '../../lib/nodeApi'
import {
  NAS_MOUNT_OPTIONS_FOCUS_QUERY,
  SMB_MOUNT_OPTION_EXAMPLES,
} from '../../lib/nasMountTroubleshooting'
import { proxyAgentsRoute } from '../../lib/nodeDeployRoutes'
import type { ApiNode } from '../../types/node'
import NasProxyTopology from './NasProxyTopology.vue'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'

type NasProtocol = 'smb' | 'nfs'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()

const repoId = computed(() => Number(route.params.id))
const busy = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
const loading = ref(true)
const loadError = ref('')
const original = ref<StorageRepository | null>(null)
const proxyNodes = ref<ApiNode[]>([])
const proxyNodesRefreshing = ref(false)
const associatedSourcesCount = ref(0)
const associatedSourcesLoading = ref(false)

const protocol = ref<NasProtocol>('smb')
const displayName = ref('')

const nasServer = ref('')
const nasShare = ref('')
const nasMountOptionsCurrent = ref('')
const mountOptionsDraft = ref('')
const mountOptionsInputRef = ref<InputInstance>()
const repositoryServerHost = ref('')

const credentialMask = '\u2022\u2022\u2022\u2022\u2022\u2022'
const hasSmbUsername = ref(false)
const hasSmbPassword = ref(false)
const smbUsernameRewriting = ref(false)
const smbPasswordRewriting = ref(false)
const smbUsernameDraft = ref('')
const smbPasswordDraft = ref('')
const smbDomain = ref('')

const quotaGb = ref(0)
const quotaAlertEnabled = ref(false)
const quotaAlertThreshold = ref<number | undefined>(80)

const proxyNodeId = ref<number | undefined>(undefined)
const initialProxyNodeId = ref<number | null>(null)
const busyWithBackups = ref(false)

const availableProxyNodes = computed(() =>
  proxyNodes.value.filter((node) => node.role === 'proxy' && node.availability === 'online'),
)
const isCurrentlyBound = computed(() => initialProxyNodeId.value != null)
const hasAssociatedSources = computed(() => associatedSourcesCount.value > 0)
const proxyBindingBlocked = computed(() => !isCurrentlyBound.value && hasAssociatedSources.value)
const bindProxyLeadItems = computed(() => [
  t('addNasRepo.bindProxyLeadItemRecommend'),
  t('addNasRepo.bindProxyLeadItemAfterBinding'),
  t('addNasRepo.bindProxyLeadItemSkip'),
])
const blockedBindProxyItems = computed(() => [
  t('repairNasRepo.bindBlockedByAssociatedSources', { n: associatedSourcesCount.value }),
  t('repairNasRepo.bindBlockedByAssociatedSourcesDetail'),
])
const boundProxyItems = computed(() => [
  t('repairNasRepo.hintBindBound'),
])
const bindLabel = computed(() =>
  isCurrentlyBound.value
    ? t('repairNasRepo.rebindLabel')
    : t('repairNasRepo.bindLabel'),
)
const accessPathLabel = computed(() =>
  proxyNodeId.value
    ? t('addNasRepo.accessPathWithProxy')
    : t('addNasRepo.accessPathDirect'),
)
const isDirectNasAccess = computed(() => !isCurrentlyBound.value && !proxyNodeId.value)
const topologySourceLabels = computed(() => [
  t('addNasRepo.demoBackupSourceA'),
  t('addNasRepo.demoBackupSourceB'),
  t('addNasRepo.demoBackupSourceC'),
])
const submitLabel = computed(() => {
  if (!isCurrentlyBound.value && proxyNodeId.value) {
    return t('repairNasRepo.btnSaveAndInit')
  }
  return t('repairNasRepo.btnSave')
})
const currentProxyName = computed(() => {
  const id = initialProxyNodeId.value
  if (!id) return t('repairNasRepo.labelUnbound')
  return (
    availableProxyNodes.value.find((n) => n.id === id)?.name ?? `#${id}`
  )
})
const smbUsernameMasked = computed(() => (hasSmbUsername.value ? credentialMask : '\u2014'))
const smbPasswordMasked = computed(() => (hasSmbPassword.value ? credentialMask : '\u2014'))

function extractConfigString(
  config: Record<string, unknown> | undefined,
  key: string,
): string {
  if (!config) return ''
  const v = config[key]
  return v == null ? '' : String(v)
}

async function loadRepository() {
  loading.value = true
  loadError.value = ''
  try {
    const repo = await getStorageRepository(repoId.value)
    original.value = repo
    protocol.value = (repo.nas_protocol === 'nfs' ? 'nfs' : 'smb') as NasProtocol
    const cfg = (repo.config || {}) as Record<string, unknown>
    displayName.value = repo.name
    nasServer.value = extractConfigString(cfg, 'server_address')
    nasShare.value = extractConfigString(cfg, 'share_path')
    nasMountOptionsCurrent.value = extractConfigString(cfg, 'mount_options')
    mountOptionsDraft.value = nasMountOptionsCurrent.value
    repositoryServerHost.value = extractConfigString(cfg, 'proxy_repository_server_host')
    hasSmbUsername.value = Boolean(extractConfigString(cfg, 'smb_username'))
    hasSmbPassword.value = Boolean(extractConfigString(cfg, 'smb_password'))
    smbUsernameRewriting.value = false
    smbPasswordRewriting.value = false
    smbUsernameDraft.value = ''
    smbPasswordDraft.value = ''
    smbDomain.value = extractConfigString(cfg, 'smb_domain')
    quotaGb.value = Number(cfg.quota_gb || 0) || 0
    quotaAlertEnabled.value = Boolean(cfg.quota_alert_enabled)
    if (typeof cfg.quota_alert_threshold === 'number') {
      quotaAlertThreshold.value = cfg.quota_alert_threshold
    }
    initialProxyNodeId.value =
      repo.bind_node_type === 'proxy' && repo.bind_node_id
        ? Number(repo.bind_node_id)
        : null
    proxyNodeId.value = initialProxyNodeId.value ?? undefined
  } catch (err) {
    loadError.value = apiErrorMessage(err, t('repairNasRepo.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function loadProxyNodes() {
  try {
    proxyNodes.value = await listAllNodes()
  } catch {
    proxyNodes.value = []
  }
}

async function loadAssociatedSourceCount() {
  associatedSourcesLoading.value = true
  try {
    const result = await listStorageRepositoryAssociatedSources(repoId.value, {
      page: 1,
      page_size: 1,
    })
    associatedSourcesCount.value = result.count
  } catch {
    associatedSourcesCount.value = 0
  } finally {
    associatedSourcesLoading.value = false
  }
}

async function refreshProxyNodesManually() {
  proxyNodesRefreshing.value = true
  try {
    await loadProxyNodes()
    ElMessage.success({ message: t('addNasRepo.proxyRefreshSuccess'), grouping: true })
  } finally {
    proxyNodesRefreshing.value = false
  }
}

function openProxyDeploy() {
  const { href } = router.resolve(proxyAgentsRoute())
  window.open(href, '_blank', 'noopener,noreferrer')
}

function validateForm(): string | null {
  if (!displayName.value.trim()) {
    return 'Enter a display name'
  }
  if (quotaGb.value < 0) {
    return 'Quota cannot be negative'
  }
  if (quotaAlertEnabled.value) {
    const v = Number(quotaAlertThreshold.value || 0)
    if (v < 1 || v > 100) {
      return 'Alert threshold must be between 1 and 100'
    }
  }
  if (isCurrentlyBound.value) {
    if (!proxyNodeId.value) {
      return t('repairNasRepo.missingRebindSelection')
    }
  } else if (proxyNodeId.value !== undefined && proxyNodeId.value <= 0) {
    proxyNodeId.value = undefined
  }
  if (proxyBindingBlocked.value && proxyNodeId.value) {
    return t('repairNasRepo.bindBlockedByAssociatedSources', { n: associatedSourcesCount.value })
  }
  if (protocol.value === 'smb') {
    if (smbUsernameRewriting.value && !smbUsernameDraft.value.trim()) {
      return 'Enter SMB username'
    }
    if (smbPasswordRewriting.value && !smbPasswordDraft.value) {
      return 'Enter SMB password'
    }
  }
  return null
}

function startRewriteSmbUsername() {
  smbUsernameRewriting.value = true
  smbUsernameDraft.value = ''
}

function cancelRewriteSmbUsername() {
  smbUsernameRewriting.value = false
  smbUsernameDraft.value = ''
}

function startRewriteSmbPassword() {
  smbPasswordRewriting.value = true
  smbPasswordDraft.value = ''
}

function cancelRewriteSmbPassword() {
  smbPasswordRewriting.value = false
  smbPasswordDraft.value = ''
}

function shouldFocusMountOptions() {
  return route.query.focus === NAS_MOUNT_OPTIONS_FOCUS_QUERY || route.hash === '#nas-mount-options'
}

async function focusMountOptions() {
  await nextTick()
  const section = document.getElementById('nas-mount-options')
  section?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.setTimeout(() => mountOptionsInputRef.value?.focus(), 250)
}

async function onSubmit() {
  if (!original.value) return
  if (!validateInline([
    { field: 'displayName', message: t('repositoriesPage.errName'), valid: !!displayName.value.trim() },
    { field: 'quotaThreshold', message: t('repositoriesPage.hintQuotaAlertThreshold'), valid: !quotaAlertEnabled.value || (Number(quotaAlertThreshold.value || 0) >= 1 && Number(quotaAlertThreshold.value || 0) <= 100) },
  ])) return
  const validationError = validateForm()
  if (validationError) {
    ElMessage.warning({ message: validationError, grouping: true })
    return
  }
  busy.value = true
  busyWithBackups.value = false
  try {
    const config: Record<string, unknown> = {
      mount_options: mountOptionsDraft.value.trim() || undefined,
      quota_gb: quotaGb.value || 0,
      quota_alert_enabled: quotaAlertEnabled.value,
      quota_alert_threshold: quotaAlertEnabled.value
        ? Number(quotaAlertThreshold.value || 0)
        : 0,
      proxy_repository_server_host: proxyNodeId.value
        ? repositoryServerHost.value.trim()
        : undefined,
    }
    if (protocol.value === 'smb') {
      if (smbUsernameRewriting.value && smbUsernameDraft.value.trim()) {
        config.smb_username = smbUsernameDraft.value.trim()
      }
      if (smbPasswordRewriting.value && smbPasswordDraft.value) {
        config.smb_password = smbPasswordDraft.value
      }
      if (smbDomain.value.trim()) {
        config.smb_domain = smbDomain.value.trim()
      }
    }
    const payload: {
      name?: string
      config?: Record<string, unknown>
      bind_node_id?: number | null
    } = {
      name: displayName.value.trim(),
      config,
    }
    if (isCurrentlyBound.value) {
      if (proxyNodeId.value && proxyNodeId.value !== initialProxyNodeId.value) {
        payload.bind_node_id = proxyNodeId.value
      }
    } else if (proxyNodeId.value) {
      payload.bind_node_id = proxyNodeId.value
    } else {
      payload.bind_node_id = null
    }
    const repaired = await repairStorageRepository(repoId.value, payload)
    const accepted = String(repaired.status || '').toLowerCase() === 'creating'
    ElMessage.success({
      message: t(accepted ? 'repairNasRepo.acceptedAsync' : 'repairNasRepo.savedOk'),
      grouping: true,
    })
    router.push({
      path: '/node/repositories',
      query: {
        tab: 'nas',
        refresh: '1',
        ...(accepted && repaired.active_create_task?.task_uuid
          ? {
              pending_create_id: String(repaired.id),
              pending_create_task: repaired.active_create_task.task_uuid,
            }
          : {}),
      },
    })
  } catch (err) {
    const message = apiErrorMessage(err, t('repairNasRepo.saveFailed'))
    if (typeof message === 'string' && /running|busy|backup/i.test(message)) {
      busyWithBackups.value = true
    }
    ElMessage.error({ message: message, grouping: true })
  } finally {
    busy.value = false
  }
}

function handleBack() {
  router.push({ path: '/node/repositories', query: { tab: 'nas' } })
}

let leaveGuardAttached = false
function attachLeaveGuard() {
  if (leaveGuardAttached) return
  leaveGuardAttached = true
  const remove = router.beforeEach((_to, _from, next) => {
    if (busy.value) {
      ElMessageBox.confirm(
        'Repair is still running. Leave anyway?',
        'Leave confirmation',
        { type: 'warning' },
      )
        .then(() => next())
        .catch(() => next(false))
    } else {
      next()
    }
  })
  onBeforeUnmount(() => remove())
}

watch(quotaAlertEnabled, (enabled) => {
  if (enabled && (quotaAlertThreshold.value == null || quotaAlertThreshold.value === 0)) {
    quotaAlertThreshold.value = 80
  }
})

onMounted(async () => {
  attachLeaveGuard()
  await Promise.all([loadRepository(), loadProxyNodes(), loadAssociatedSourceCount()])
  if (shouldFocusMountOptions()) void focusMountOptions()
})
</script>

<template>
  <div ref="pageRef" class="fullscreen-form-fullscreen resource-add-fullscreen">
    <div class="fullscreen-form-page add-nas-page">
      <header class="fullscreen-form-header">
        <button class="fullscreen-form-header__back" @click="handleBack">
          <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            <Wrench :size="18" class="inline-block align-[-3px] mr-1 text-[rgb(37_99_235)]" />
            {{ t('repairNasRepo.pageTitle') }}
          </h1>
          <p class="fullscreen-form-header__desc">{{ t('repairNasRepo.pageDesc') }}</p>
        </div>
      </header>

      <div v-if="loading" class="p-10 text-center text-sm text-[rgb(100_116_139)]">
        {{ t('common.loading') }}
      </div>
      <div v-else-if="loadError" class="p-10 text-center text-sm text-red-500">
        {{ loadError }}
      </div>

      <div v-else class="fullscreen-form-layout add-nas-layout">
        <div class="fullscreen-form-main add-nas-main">
          <section class="add-nas-card add-nas-step-section">
            <div class="add-nas-section">
              <h3 class="add-nas-section__title">
                <span class="add-nas-section__indicator" />
                {{ t('repairNasRepo.sectionNasReadonly') }}
              </h3>
              <ElForm label-position="top" class="add-nas-form">
                <div class="add-nas-form-row add-nas-form-row--responsive">
                  <ElFormItem :label="t('repairNasRepo.labelProtocol')" class="flex-1">
                    <ElTag size="large" effect="plain" class="!text-sm">
                      <component :is="nasMountProtocolIcon(protocol)" :size="14" class="inline-block align-[-2px] mr-1" />
                      {{ protocol === 'smb' ? t('repositoriesPage.protocolSmb') : t('repositoriesPage.protocolNfs') }}
                    </ElTag>
                  </ElFormItem>
                  <ElFormItem :label="t('repairNasRepo.labelServer')" class="flex-1">
                    <ElInput :model-value="nasServer" disabled />
                  </ElFormItem>
                  <ElFormItem
                    :label="protocol === 'smb' ? t('repairNasRepo.labelShareName') : t('repairNasRepo.labelExportPath')"
                    class="flex-1"
                  >
                    <ElInput :model-value="nasShare" disabled />
                  </ElFormItem>
                </div>
                <p class="text-xs text-[rgb(100_116_139)]">
                  {{ t('repairNasRepo.readonlyHint') }}
                </p>
              </ElForm>
            </div>
          </section>

          <section v-if="protocol === 'smb'" class="add-nas-card add-nas-step-section">
            <div class="add-nas-section">
              <h3 class="add-nas-section__title">
                <span class="add-nas-section__indicator" />
                <ShieldCheck :size="16" class="inline-block align-[-3px] mr-1" />
                {{ t('repairNasRepo.smbSection') }}
              </h3>
              <ElForm label-position="top" class="add-nas-form">
                <div class="add-nas-form-row add-nas-form-row--responsive">
                  <ElFormItem :label="t('repositoriesPage.fieldSmbUsername')" class="flex-1">
                    <div
                      v-if="!smbUsernameRewriting"
                      class="repair-nas-credential"
                    >
                      <ElInput
                        :model-value="smbUsernameMasked"
                        readonly
                        disabled
                      />
                      <ElButton
                        size="small"
                        @click="startRewriteSmbUsername"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnRewrite') }}
                      </ElButton>
                    </div>
                    <div
                      v-else
                      class="repair-nas-credential"
                    >
                      <ElInput
                        v-model="smbUsernameDraft"
                        :placeholder="t('repositoriesPage.phSmbUsername')"
                      />
                      <ElButton
                        size="small"
                        @click="cancelRewriteSmbUsername"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnCancel') }}
                      </ElButton>
                    </div>
                  </ElFormItem>
                  <ElFormItem :label="t('repositoriesPage.fieldSmbPassword')" class="flex-1">
                    <div
                      v-if="!smbPasswordRewriting"
                      class="repair-nas-credential"
                    >
                      <ElInput
                        :model-value="smbPasswordMasked"
                        type="password"
                        readonly
                        disabled
                      />
                      <ElButton
                        size="small"
                        @click="startRewriteSmbPassword"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnRewrite') }}
                      </ElButton>
                    </div>
                    <div
                      v-else
                      class="repair-nas-credential"
                    >
                      <ElInput
                        v-model="smbPasswordDraft"
                        type="password"
                        show-password
                        :placeholder="t('repositoriesPage.phSmbPassword')"
                      />
                      <ElButton
                        size="small"
                        @click="cancelRewriteSmbPassword"
                      >
                        {{ t('repositoriesPage.editS3Repo.btnCancel') }}
                      </ElButton>
                    </div>
                  </ElFormItem>
                  <ElFormItem :label="t('repositoriesPage.fieldSmbDomain')" class="flex-1">
                    <ElInput v-model="smbDomain" :placeholder="t('repositoriesPage.phSmbDomain')" />
                  </ElFormItem>
                </div>
              </ElForm>
            </div>
          </section>

          <section class="add-nas-card add-nas-step-section">
            <div class="add-nas-section">
              <h3 class="add-nas-section__title">
                <span class="add-nas-section__indicator" />
                {{ t('repairNasRepo.sectionRepo') }}
              </h3>
              <ElForm label-position="top" class="add-nas-form">
                <ElFormItem data-validation-field="displayName" :error="errors.displayName" :label="t('repairNasRepo.labelDisplayName')" required>
                  <ElInput v-model="displayName" :placeholder="t('repairNasRepo.phDisplayName')" @input="clearFieldError('displayName')" />
                </ElFormItem>
                <ElFormItem id="nas-mount-options" :label="t('repairNasRepo.labelMountOptions')">
                  <ElInput
                    ref="mountOptionsInputRef"
                    v-model="mountOptionsDraft"
                    :placeholder="protocol === 'smb' ? 'vers=3.0' : t('addNasRepo.phMountOptionsNfs')"
                  />
                  <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                    {{ protocol === 'smb'
                      ? 'Saved value replaces the current mount options. If SMB auto-negotiation fails, copy one example below and test.'
                      : t('repairNasRepo.hintMountOptions') }}
                  </div>
                  <ElAlert v-if="protocol === 'smb'" type="warning" :closable="false" show-icon class="mt-2">
                    <div class="text-xs leading-5 text-[rgb(51_65_85)]">
                      <div>
                        Different NAS devices, SMB servers, and client kernels may support different protocol versions. For Operation not supported, Invalid argument, or mount.cifs errors, specify a protocol version here.
                      </div>
                      <div class="mt-1 flex flex-wrap gap-1.5">
                        <code
                          v-for="example in SMB_MOUNT_OPTION_EXAMPLES"
                          :key="example"
                          class="rounded border border-[rgb(203_213_225)] bg-white px-1.5 py-0.5 font-mono text-[11px] text-[rgb(15_23_42)]"
                        >
                          {{ example }}
                        </code>
                      </div>
                    </div>
                  </ElAlert>
                </ElFormItem>
              </ElForm>
            </div>
          </section>

          <section class="add-nas-card add-nas-step-section">
            <div class="add-nas-section">
              <h3 class="add-nas-section__title">
                <span class="add-nas-section__indicator" />
                {{ t('repairNasRepo.sectionQuota') }}
              </h3>
              <ElForm label-position="top" class="add-nas-form">
                <div class="add-nas-form-row add-nas-form-row--responsive">
                  <div class="fullscreen-form-field add-nas-quota-col">
                    <label class="fullscreen-form-field__label add-nas-quota-head">
                      {{ t('repairNasRepo.labelQuota') }}
                    </label>
                    <div class="add-nas-quota-control">
                      <div class="hfl-detail-form-input hfl-detail-form-input--narrow add-nas-quota-input">
                        <ElInputNumber
                          v-model="quotaGb"
                          class="hfl-detail-form-input__num"
                          :placeholder="t('repairNasRepo.phQuota')"
                          :min="0"
                          controls-position="right"
                        />
                        <div class="hfl-detail-form-input__suffix">GB</div>
                      </div>
                    </div>
                  </div>
                  <div data-validation-field="quotaThreshold" class="fullscreen-form-field add-nas-quota-col add-nas-quota-panel">
                    <div class="fullscreen-form-field__label add-nas-quota-head add-nas-quota-title-row">
                      <ElCheckbox v-model="quotaAlertEnabled">
                        {{ t('repairNasRepo.labelQuotaAlert') }}
                      </ElCheckbox>
                    </div>
                    <div class="add-nas-quota-control">
                      <div class="hfl-detail-form-input hfl-detail-form-input--narrow add-nas-quota-input">
                        <ElInputNumber
                          v-model="quotaAlertThreshold"
                          class="hfl-detail-form-input__num"
                          :min="1"
                          :max="100"
                          :disabled="!quotaAlertEnabled"
                          controls-position="right"
                          @change="clearFieldError('quotaThreshold')"
                        />
                        <div class="hfl-detail-form-input__suffix">%</div>
                      </div>
                    </div>
                    <p class="fullscreen-form-field__hint">
                      {{ t('repairNasRepo.labelQuotaAlertThreshold') }}
                    </p>
                    <p v-if="errors.quotaThreshold" class="el-form-item__error">{{ errors.quotaThreshold }}</p>
                  </div>
                </div>
              </ElForm>
            </div>
          </section>

          <section class="add-nas-card add-nas-step-section">
            <div class="add-nas-section">
              <div class="add-nas-section__head">
                <div class="add-nas-section__title-wrap">
                  <h3 class="add-nas-section__title !mb-0">
                    <span class="add-nas-section__indicator" />
                    {{ bindLabel }}
                  </h3>
                  <span v-if="!isCurrentlyBound" class="add-nas-optional-badge">{{ t('addNasRepo.optional') }}</span>
                </div>
                <ElButton class="add-nas-proxy-action" @click="openProxyDeploy">
                  <Plus :size="14" />
                  {{ t('addNasRepo.deployProxy') }}
                </ElButton>
              </div>

              <ElAlert
                type="warning"
                :closable="false"
                class="add-nas-proxy-alert"
              >
                <ol class="add-nas-proxy-alert__list">
                  <li
                    v-for="(item, index) in busyWithBackups
                      ? [t('repairNasRepo.hintBusy')]
                      : proxyBindingBlocked
                        ? blockedBindProxyItems
                        : isCurrentlyBound
                          ? boundProxyItems
                          : bindProxyLeadItems"
                    :key="item"
                    class="add-nas-proxy-alert__item"
                  >
                    <span class="add-nas-proxy-alert__index">{{ index + 1 }}</span>
                    <span class="add-nas-proxy-alert__text">{{ item }}</span>
                  </li>
                </ol>
              </ElAlert>

              <div class="add-nas-proxy-layout">
                <div class="add-nas-proxy-form">
                  <ElForm label-position="top" class="add-nas-form">
                    <ElFormItem class="add-nas-bind-form-item">
                      <template #label>{{ t('addNasRepo.fieldSourceProxyNode') }}</template>
                      <div class="add-nas-select-row">
                        <ElSelect
                          v-model="proxyNodeId"
                          class="add-nas-select-row__select"
                          :clearable="!isCurrentlyBound"
                          filterable
                          :disabled="proxyBindingBlocked || associatedSourcesLoading"
                          :placeholder="t('repairNasRepo.phBindProxy')"
                        >
                          <template v-if="!isCurrentlyBound">
                            <ElOption :value="undefined" :label="t('repairNasRepo.optionNoProxy')" />
                          </template>
                          <ElOption
                            v-for="n in availableProxyNodes"
                            :key="n.id"
                            :value="n.id"
                            :label="n.ip_address ? `${n.name} (${n.ip_address})` : n.name"
                          />
                        </ElSelect>
                        <ElButton
                          class="hfl-refresh-button add-nas-select-row__refresh"
                          :title="t('addNasRepo.proxyRefresh')"
                          :aria-label="t('addNasRepo.proxyRefresh')"
                          :disabled="proxyNodesRefreshing"
                          @click="refreshProxyNodesManually"
                        >
                          <RefreshCw :size="16" :class="{ 'is-spinning': proxyNodesRefreshing }" />
                        </ElButton>
                      </div>
                      <div class="mt-2 text-xs text-[rgb(100_116_139)]">
                        <template v-if="isCurrentlyBound">
                          {{ t('repairNasRepo.labelCurrentProxy') }}: {{ currentProxyName }}
                        </template>
                        <template v-else>
                          {{ accessPathLabel }}
                        </template>
                      </div>
                    </ElFormItem>
                    <ElFormItem
                      v-if="proxyNodeId"
                      :label="t('repositoriesPage.fieldRepositoryServerHost')"
                    >
                      <ElInput
                        v-model="repositoryServerHost"
                        :placeholder="t('repositoriesPage.phRepositoryServerHost')"
                      />
                      <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                        {{ t('repositoriesPage.hintRepositoryServerHost') }}
                      </div>
                    </ElFormItem>
                  </ElForm>
                </div>

                <NasProxyTopology
                  :direct="isDirectNasAccess"
                  :source-labels="topologySourceLabels"
                />
              </div>
            </div>
          </section>

          <div class="fullscreen-form-footer add-nas-footer">
            <div class="add-nas-footer__actions">
              <ElButton :disabled="busy" @click="handleBack">
                {{ t('repositoriesPage.btnCancel') }}
              </ElButton>
              <ElButton type="primary" :loading="busy" :disabled="busy" @click="onSubmit">
                {{ submitLabel }}
              </ElButton>
            </div>
          </div>
        </div>

        <aside class="fullscreen-form-sidebar add-form-preview-sidebar">
          <div class="add-nas-preview-card">
            <div class="add-nas-preview-header">
              <div class="add-nas-preview-header__glow" />
              <div class="add-nas-preview-header__icon">
                <component :is="nasMountProtocolIcon(protocol)" class="add-nas-preview-header__drive" :size="28" />
              </div>
              <div class="add-nas-preview-header__info">
                <h4 class="add-nas-preview-header__name">
                  {{ displayName || t('common.empty') }}
                </h4>
                <p class="add-nas-preview-header__type">
                  {{ protocol === 'smb' ? t('repositoriesPage.protocolSmb') : t('repositoriesPage.protocolNfs') }}
                </p>
              </div>
            </div>
            <div class="add-nas-preview-body">
              <div class="add-nas-preview-section">
                <h5 class="add-nas-preview-section__title">{{ t('addS3Repo.previewBasicInfo') }}</h5>
                <div class="add-nas-preview-row">
                  <span class="add-nas-preview-row__label">{{ t('repairNasRepo.labelDisplayName') }}</span>
                  <span class="add-nas-preview-row__value" :class="{ 'add-nas-preview-row__value--empty': !displayName }">
                    {{ displayName || '—' }}
                  </span>
                </div>
                <div class="add-nas-preview-row">
                  <span class="add-nas-preview-row__label">{{ t('addNasRepo.fieldSourceProxyNode') }}</span>
                  <span class="add-nas-preview-row__value" :class="{ 'add-nas-preview-row__value--empty': !proxyNodeId }">
                    {{ availableProxyNodes.find((n) => n.id === proxyNodeId)?.name || t('addNasRepo.notBoundProxy') }}
                  </span>
                </div>
                <div class="add-nas-preview-row">
                  <span class="add-nas-preview-row__label">{{ t('repositoriesPage.fieldAccessPath') }}</span>
                  <span class="add-nas-preview-row__value">{{ accessPathLabel }}</span>
                </div>
                <div class="add-nas-preview-row">
                  <span class="add-nas-preview-row__label">{{ t('repairNasRepo.labelQuota') }}</span>
                  <span class="add-nas-preview-row__value" :class="{ 'add-nas-preview-row__value--highlight': quotaGb > 0 }">
                    {{ quotaGb > 0 ? `${quotaGb} GB` : t('addS3Repo.previewUnlimited') }}
                  </span>
                </div>
                <div class="add-nas-preview-row">
                  <span class="add-nas-preview-row__label">{{ t('repairNasRepo.labelQuotaAlert') }}</span>
                  <span class="add-nas-preview-row__value add-nas-preview-row__value--badge" :class="quotaAlertEnabled ? 'add-nas-preview-row__value--success' : 'add-nas-preview-row__value--muted'">
                    <span v-if="quotaAlertEnabled" class="add-nas-preview-row__dot add-nas-preview-row__dot--green" />
                    <template v-if="quotaAlertEnabled">
                      {{ t('repositoriesPage.enabled') }} ({{ quotaAlertThreshold || 0 }}%)
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
.add-nas-card {
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(248, 250, 252, 0.96) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.7),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.add-nas-page {
  display: flex;
  flex-direction: column;
}

.add-nas-layout {
  flex: 1 1 auto;
  min-height: 0;
}

.add-nas-main {
  min-height: 0;
  height: 100%;
  box-sizing: border-box;
  overflow-y: auto;
  scroll-padding: 16px 0 84px;
}

.add-nas-step-section {
  flex: 0 0 auto;
  width: 100%;
  box-sizing: border-box;
  scroll-margin-top: 16px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.add-nas-step-section--active {
  border-color: rgba(37, 99, 235, 0.55);
  box-shadow:
    inset 3px 0 0 rgba(37, 99, 235, 0.85),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

.repair-nas-credential {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.repair-nas-credential :deep(.el-input) {
  flex: 1;
}

.add-nas-card.add-nas-step-section--active {
  border-color: rgba(37, 99, 235, 0.55);
  box-shadow:
    inset 3px 0 0 rgba(37, 99, 235, 0.85),
    0 8px 20px rgba(15, 23, 42, 0.04);
}

@media (min-width: 1280px) {
  .add-nas-layout {
    flex-direction: row;
    align-items: flex-start;
  }
}

.add-nas-step-section--locked {
  opacity: 0.68;
  user-select: none;
}

.add-nas-step-section--locked :deep(.el-input),
.add-nas-step-section--locked :deep(.el-select),
.add-nas-step-section--locked :deep(.el-radio),
.add-nas-step-section--locked :deep(.el-checkbox),
.add-nas-step-section--locked :deep(.el-button),
.add-nas-step-section--locked button {
  pointer-events: none;
}

.add-nas-section {
  padding: 18px 20px 20px;
}

.add-nas-section__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: rgb(15 23 42);
  margin: 0 0 18px;
}

.add-nas-section__indicator {
  width: 4px;
  height: 16px;
  border-radius: 999px;
  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
}

.add-nas-section__subtitle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(30 41 59);
  margin: 20px 0 12px;
  padding-top: 16px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.add-nas-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.add-nas-section__title-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.add-nas-section__tool-btn {
  width: 28px;
  height: 28px;
  padding: 0;
  flex-shrink: 0;
}

.add-nas-optional-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.add-nas-proxy-action {
  flex: 0 0 auto;
  white-space: nowrap;
  border-color: var(--color-primary, var(--el-color-primary)) !important;
  background-color: var(--color-primary, var(--el-color-primary)) !important;
  background-image: linear-gradient(
    var(--color-primary-gradient-start, #7E6CEF),
    var(--color-primary-gradient-end, #6D5EF6)
  ) !important;
  color: #fff !important;
  font-weight: 700;
  box-shadow: rgba(109, 94, 246, 0.32) 0 6px 16px -8px;
}

.add-nas-proxy-action:hover,
.add-nas-proxy-action:focus {
  border-color: var(--color-primary-hover-gradient-end, #7664FA) !important;
  background-color: var(--color-primary-hover-gradient-end, #7664FA) !important;
  background-image: linear-gradient(
    var(--color-primary-hover-gradient-start, #8876F5),
    var(--color-primary-hover-gradient-end, #7664FA)
  ) !important;
  color: #fff !important;
  box-shadow: rgba(109, 94, 246, 0.42) 0 8px 18px -8px;
}

.add-nas-form {
  margin-top: 4px;
  max-width: 600px;
}

.add-nas-form :deep(.el-form-item__label) {
  font-weight: 600;
  color: rgb(30 41 59);
}

.add-nas-form :deep(.el-form-item) {
  margin-bottom: 20px;
}

.add-nas-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.add-nas-form-row {
  display: flex;
  gap: 16px;
}

.add-nas-bind-form-item :deep(.el-form-item__content) {
  width: 100%;
}

.add-nas-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.add-nas-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.add-nas-select-row__refresh {
  flex: 0 0 34px;
}

.add-nas-proxy-alert {
  max-width: 780px;
  margin-bottom: 18px;
  border-color: var(--color-warning-border) !important;
  border-radius: 6px !important;
  background: var(--color-warning-light) !important;
  color: var(--color-warning) !important;
}

.add-nas-proxy-alert :deep(.el-alert__content) {
  min-width: 0;
  padding: 0 4px;
}

.add-nas-proxy-alert :deep(.el-alert__description),
.add-nas-proxy-alert :deep(.el-alert__title) {
  color: var(--color-warning);
  font-size: 13px;
  line-height: 1.55;
}

.add-nas-proxy-alert__list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.add-nas-proxy-alert__item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: flex-start;
  gap: 8px;
}

.add-nas-proxy-alert__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid var(--color-warning-border);
  border-radius: 999px;
  background: #fff;
  color: var(--color-warning);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.add-nas-proxy-alert__text {
  min-width: 0;
  color: rgb(120 75 12);
  font-size: 13px;
  line-height: 1.55;
}

.add-nas-proxy-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 420px);
  gap: 18px;
  align-items: stretch;
}

.add-nas-proxy-form {
  min-width: 0;
}

.add-nas-proxy-benefits {
  display: grid;
  gap: 10px;
  max-width: 620px;
  margin-top: 2px;
}

.add-nas-proxy-benefit {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(248, 250, 252, 0.78);
  font-size: 13px;
  line-height: 1.5;
  color: rgb(51 65 85);
}

.add-nas-proxy-benefit__dot {
  width: 7px;
  height: 7px;
  margin-top: 7px;
  flex-shrink: 0;
  border-radius: 999px;
  background: rgb(37 99 235);
}

.add-nas-deploy-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding-inline: 14px;
  border-radius: 10px;
  margin-top: 16px;
}

.agent-deploy-body {
  max-height: 60vh;
  overflow-y: auto;
}

.proxy-deploy-dialog__alert {
  margin-bottom: 16px;
  border-radius: 12px !important;
}

.proxy-deploy-dialog__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.proxy-deploy-dialog__desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: rgb(100 116 139);
}

.source-script-shell {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  padding: 14px;
  border: 1px solid #0f172a;
  border-radius: 12px;
  background: linear-gradient(180deg, #111827 0%, #0f172a 100%);
}

.source-script-shell--compact {
  padding: 12px;
}

.source-script-shell__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.source-script-shell__label {
  font-size: 12px;
  color: #94a3b8;
}

.source-script-shell__content {
  max-height: min(32vh, 320px);
  margin: 0;
  overflow: auto;
  padding-right: 6px;
  color: #86efac;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 11px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
}

.source-script-shell__content--dialog {
  max-height: min(24vh, 220px);
}

.add-nas-protocol-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}

.add-nas-protocol-card {
  height: auto !important;
  min-height: 58px;
  padding: 10px 14px !important;
  border-radius: 10px !important;
  border-color: #c8d3e0 !important;
  background: #f8fbff;
  transition: all 0.18s ease;
}

.add-nas-protocol-card:hover {
  border-color: #7a99bc !important;
  background: #f1f6fc;
  box-shadow: 0 8px 16px rgba(59, 130, 246, 0.08);
}

.add-nas-protocol-card.is-checked {
  border-color: var(--color-primary, #457AB0) !important;
  background: rgba(69, 125, 176, 0.14);
  color: var(--color-primary, #457AB0);
}

.add-nas-protocol-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #334155;
}

.add-nas-protocol-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 8px;
  background: rgba(69, 125, 176, 0.1);
  color: #457AB0;
  transition: all 0.18s ease;
}

.add-nas-protocol-card:hover .add-nas-protocol-card__icon,
.add-nas-protocol-card.is-checked .add-nas-protocol-card__icon {
  background: rgba(69, 125, 176, 0.16);
  color: var(--color-primary, #457AB0);
}

.add-nas-protocol-card__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.add-nas-quota-col {
  flex: 1 1 0;
  display: grid;
  grid-template-rows: 22px 32px auto;
  gap: 6px;
  align-content: start;
  min-width: 0;
}

.add-nas-quota-head {
  display: flex;
  align-items: center;
  min-height: 22px;
  margin: 0;
}

.add-nas-quota-panel {
  padding-left: 20px;
}

.add-nas-quota-title-row :deep(.el-checkbox) {
  --el-checkbox-height: 22px;
  align-items: center;
  height: 22px;
  min-height: 22px;
  margin-right: 0;
}

.add-nas-quota-title-row :deep(.el-checkbox__label) {
  line-height: 22px;
  color: rgb(30 41 59);
}

.add-nas-quota-control {
  display: flex;
  align-items: center;
  height: 32px;
}

.add-nas-quota-input {
  width: 200px;
  max-width: 100%;
}

.add-nas-quota-threshold-block {
  margin-top: 8px;
}

.add-nas-quota-threshold-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 32px;
}

.add-nas-quota-threshold-row__label {
  flex-shrink: 0;
  font-size: 14px;
  color: rgb(30 41 59);
}

.add-nas-quota-threshold {
  display: flex;
  align-items: center;
  gap: 8px;
}

.add-nas-quota-threshold__input {
  width: 120px;
}

.add-nas-quota-threshold__unit {
  font-size: 14px;
  font-weight: 500;
  color: rgb(100 116 139);
}

.add-nas-review {
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.74);
}

.add-nas-review-card {
  width: 100%;
}

.add-nas-preview-card--inline {
  width: 100%;
  border-radius: 8px;
}

.add-nas-review__desc :deep(.el-descriptions__label) {
  font-weight: 500;
}

.add-nas-footer__step {
  min-width: 0;
  margin: 0 auto 0 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary, rgb(30 41 59));
}

.add-nas-footer__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.add-nas-preview-card {
  background: #fff;
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
}

.add-nas-preview-header {
  position: relative;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa, #ebeef5);
  border-bottom: 1px solid rgba(203, 213, 225, 0.9);
  overflow: hidden;
}

.add-nas-preview-header__glow {
  position: absolute;
  top: -40px;
  right: -40px;
  width: 120px;
  height: 120px;
  background: radial-gradient(circle, rgba(69, 125, 176, 0.15), transparent 70%);
  border-radius: 50%;
  filter: blur(20px);
}

.add-nas-preview-header__icon {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  margin-bottom: 14px;
  background: #fff;
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 12px;
}

.add-nas-preview-header__drive {
  color: var(--color-primary, #457ab0);
}

.add-nas-preview-header__name {
  margin: 0 0 4px;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.3;
  color: rgb(30 41 59);
}

.add-nas-preview-header__type {
  margin: 0;
  font-size: 13px;
  color: rgb(100 116 139);
}

.add-nas-preview-body {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 18px;
  padding: 20px;
}

.add-nas-preview-section {
  min-width: 0;
  padding: 16px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: rgb(248 250 252 / 0.72);
}

.add-nas-preview-section:last-child {
  margin-bottom: 0;
}

.add-nas-preview-section__title {
  margin: 0 0 12px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgb(100 116 139);
}

.add-nas-preview-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}

.add-nas-preview-row:last-child {
  margin-bottom: 0;
}

.add-nas-preview-row__label {
  flex-shrink: 0;
  font-size: 13px;
  color: rgb(100 116 139);
}

.add-nas-preview-row__value {
  font-size: 13px;
  font-weight: 500;
  color: rgb(30 41 59);
  text-align: right;
  word-break: break-all;
}

.add-nas-preview-row__value--empty {
  color: rgb(148 163 184);
  font-style: italic;
}

.add-nas-preview-row__value--mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.add-nas-preview-row__value--highlight {
  color: var(--color-primary, #457ab0);
}

.add-nas-preview-row__value--primary {
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(69, 125, 176, 0.1);
  color: var(--color-primary, #457ab0);
}

.add-nas-preview-row__value--badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.add-nas-preview-row__value--success {
  color: var(--color-success-text);
}

.add-nas-preview-row__value--muted {
  color: rgb(148 163 184);
}

.add-nas-preview-row__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.add-nas-preview-row__dot--green {
  background: var(--color-success);
  box-shadow: 0 0 6px var(--color-success);
}

@media (max-width: 768px) {
  .add-nas-section {
    padding: 16px;
  }

  .add-nas-form-row--responsive {
    flex-direction: column;
    gap: 0;
  }

  .add-nas-protocol-grid {
    grid-template-columns: 1fr;
  }

  .add-nas-proxy-action {
    width: 100%;
  }

  .add-nas-quota-panel {
    padding-left: 0;
  }

  .add-nas-proxy-layout {
    grid-template-columns: 1fr;
  }

  .add-nas-quota-threshold-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .add-nas-quota-threshold-block {
    margin-top: 12px;
  }

}
</style>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
