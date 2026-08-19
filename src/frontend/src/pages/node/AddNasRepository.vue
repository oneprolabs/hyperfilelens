<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Plus, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import {
  nasMountProtocolIcon,
} from '../../lib/resourceIcons'
import {
  createStorageRepository,
  storageRepositoryCreateErrorMessage,
  type StorageRepository,
  type StorageRepositoryCreatePayload,
} from '../../lib/storageRepositoryApi'
import { listAllNodes } from '../../lib/nodeApi'
import { SMB_MOUNT_OPTION_EXAMPLES } from '../../lib/nasMountTroubleshooting'
import { defaultNasMountOptions } from '../../lib/nasMountOptions'
import { preflightNasRepositoryCreate, showNasDraftPreflightGuidance } from '../../lib/nasDraftPreflight'
import { proxyAgentsRoute } from '../../lib/nodeDeployRoutes'
import type { ApiNode } from '../../types/node'
import NasProxyTopology from './NasProxyTopology.vue'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import {
  REPOSITORY_QUOTA_UNITS,
  repositoryQuotaToGb,
  type RepositoryQuotaUnit,
} from '../../lib/repositoryQuota'

type NasProtocol = 'smb' | 'nfs'

const { t } = useI18n()
const router = useRouter()
const bindProxyLeadItems = computed(() => [
  t('addNasRepo.bindProxyLeadItemRecommend'),
  t('addNasRepo.bindProxyLeadItemAfterBinding'),
  t('addNasRepo.bindProxyLeadItemDirectLinuxOnly'),
])
const directAccessRiskItems = computed(() => [
  t('addNasRepo.directAccessRiskLinuxOnly'),
  t('addNasRepo.directAccessRiskDependencies'),
  t('addNasRepo.directAccessRiskDifferences'),
  t('addNasRepo.directAccessRiskBindProxy'),
])
const props = withDefaults(defineProps<{
  embedded?: boolean
}>(), {
  embedded: false,
})
const emit = defineEmits<{
  cancel: []
  created: [payload: StorageRepository]
}>()

/* ---------- form state ---------- */
const busy = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)
const proxyNodesRefreshing = ref(false)
const proxyNodes = ref<ApiNode[]>([])

const protocol = ref<NasProtocol>('smb')

/* SMB auth */
const smbHost = ref('')
const smbShare = ref('')
const mountOptions = ref(defaultNasMountOptions('smb'))
const smbUsername = ref('')
const smbPassword = ref('')
const smbDomain = ref('')

/* NFS auth */
const nfsHost = ref('')
const nfsExport = ref('')

/* Step 1: repo info */
const repoName = ref('')
const quota = ref(0)
const quotaUnit = ref<RepositoryQuotaUnit>('GB')
const enableQuotaAlert = ref(false)
const quotaAlertThreshold = ref<number | undefined>(undefined)
const proxyNodeId = ref<number | undefined>(undefined)

watch(protocol, (nextProtocol, previousProtocol) => {
  if (previousProtocol && mountOptions.value === defaultNasMountOptions(previousProtocol)) {
    mountOptions.value = defaultNasMountOptions(nextProtocol)
  }
})

const protocolLabel = computed(() =>
  protocol.value === 'smb' ? t('repositoriesPage.protocolSmb') : t('repositoriesPage.protocolNfs'),
)
const availableProxyNodes = computed(() => proxyNodes.value.filter((node) => node.role === 'proxy'))
const selectedProxyNodeName = computed(() =>
  availableProxyNodes.value.find((node) => node.id === proxyNodeId.value)?.name || '',
)
const hasProxyDecision = computed(() => proxyNodeId.value !== undefined)
const hasBoundProxy = computed(() => Number(proxyNodeId.value || 0) > 0)
const isDirectNasAccess = computed(() => proxyNodeId.value === 0)
const accessPathLabel = computed(() =>
  isDirectNasAccess.value ? t('addNasRepo.accessPathDirect') : t('addNasRepo.accessPathWithProxy'),
)
const proxyPreviewLabel = computed(() => {
  if (selectedProxyNodeName.value) return selectedProxyNodeName.value
  return isDirectNasAccess.value ? t('addNasRepo.notBoundProxy') : '—'
})
const topologySourceLabels = computed(() => [
  t('addNasRepo.demoBackupSourceA'),
  t('addNasRepo.demoBackupSourceB'),
  t('addNasRepo.demoBackupSourceC'),
])
const submitButtonLabel = computed(() =>
  isDirectNasAccess.value
    ? 'Save configuration'
    : 'Submit and initialize',
)
const validQuotaAlertThreshold = computed(() => {
  const value = Number(quotaAlertThreshold.value || 0)
  return value >= 1 && value <= 100
})

/* ---------- validation ---------- */
function validateForm(): boolean {
  const rules = [
    { field: 'smbHost', message: t('addNasRepo.errSmbHost'), valid: protocol.value !== 'smb' || !!smbHost.value.trim() },
    { field: 'smbShare', message: t('addNasRepo.errSmbShare'), valid: protocol.value !== 'smb' || !!smbShare.value.trim() },
    { field: 'smbUsername', message: t('repositoriesPage.errSmbUsername'), valid: protocol.value !== 'smb' || !!smbUsername.value.trim() },
    { field: 'smbPassword', message: t('repositoriesPage.errSmbPassword'), valid: protocol.value !== 'smb' || !!smbPassword.value.trim() },
    { field: 'nfsHost', message: t('repositoriesPage.errNfsHost'), valid: protocol.value !== 'nfs' || !!nfsHost.value.trim() },
    { field: 'nfsExport', message: t('repositoriesPage.errNfsExport'), valid: protocol.value !== 'nfs' || !!nfsExport.value.trim() },
    { field: 'proxyNode', message: t('addNasRepo.errProxyDecisionRequired'), valid: hasProxyDecision.value },
    { field: 'repoName', message: t('repositoriesPage.errName'), valid: !!repoName.value.trim() },
    { field: 'quotaThreshold', message: t('repositoriesPage.hintQuotaAlertThreshold'), valid: !enableQuotaAlert.value || validQuotaAlertThreshold.value },
  ]
  const valid = validateInline(rules)
  if (!valid && rules.find(rule => !rule.valid)?.field === 'proxyNode') {
    ElMessage.warning({ message: t('addNasRepo.errProxyDecisionRequired'), grouping: true })
  }
  return valid
}

/* ---------- submit ---------- */
async function onSubmit() {
  if (!validateForm()) return
  busy.value = true
  try {
    const payload = buildCreatePayload()
    await preflightNasRepositoryCreate(payload)
    const created = await createStorageRepository(payload)
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
          tab: 'nas',
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
    if (await showNasDraftPreflightGuidance(err, t, selectedProxyNodeName.value)) return
    ElMessage.error({ message: storageRepositoryCreateErrorMessage(err, t), grouping: true })
  } finally {
    busy.value = false
  }
}

function buildCreatePayload(): StorageRepositoryCreatePayload {
  const bindNodeId = proxyNodeId.value && proxyNodeId.value > 0 ? proxyNodeId.value : undefined
  const config: Record<string, unknown> = {
    server_address: protocol.value === 'smb' ? smbHost.value.trim() : nfsHost.value.trim(),
    share_path: protocol.value === 'smb' ? smbShare.value.trim() : nfsExport.value.trim(),
    mount_options: mountOptions.value.trim() || undefined,
    quota_gb: repositoryQuotaToGb(quota.value, quotaUnit.value),
    quota_unit: quotaUnit.value,
    quota_alert_enabled: enableQuotaAlert.value,
    quota_alert_threshold: enableQuotaAlert.value ? Number(quotaAlertThreshold.value || 0) : 0,
  }

  const payload: StorageRepositoryCreatePayload = {
    name: repoName.value.trim(),
    repo_type: 'nas',
    nas_protocol: protocol.value,
    bind_node_type: bindNodeId ? 'proxy' : undefined,
    bind_node_id: bindNodeId,
    config,
  }

  if (protocol.value === 'smb') {
    config.smb_username = smbUsername.value.trim()
    config.smb_password = smbPassword.value
    config.smb_domain = smbDomain.value.trim() || undefined
  }

  return payload
}

function handleBack() {
  if (props.embedded) {
    emit('cancel')
    return
  }
  router.push({ path: '/node/repositories', query: { tab: 'nas' } })
}

async function loadProxyNodes() {
  proxyNodes.value = await listAllNodes().catch(() => [])
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

function clearProxySelection() {
  proxyNodeId.value = undefined
}

onMounted(() => {
  void loadProxyNodes()
})

function openProxyDeploy() {
  const { href } = router.resolve(proxyAgentsRoute())
  window.open(href, '_blank', 'noopener,noreferrer')
}

watch(enableQuotaAlert, (enabled) => {
  if (!enabled) {
    quotaAlertThreshold.value = undefined
    return
  }
  if (!quotaAlertThreshold.value) quotaAlertThreshold.value = 80
})

</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen"
    :class="{ 'resource-add-fullscreen--embedded': embedded }"
  >
    <div class="fullscreen-form-page add-nas-page">
      <header
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
            {{ t('repositoriesPage.addNasPage') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('repositoriesPage.addNasPageDesc') }}
          </p>
        </div>
      </header>

      <div class="fullscreen-form-layout">
        <div class="fullscreen-form-main add-nas-main">
          <div class="fullscreen-form-step-stack">
            <section
              class="fullscreen-form-card fullscreen-form-section add-nas-step-section"
            >
              <div>
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('repositoriesPage.fieldProtocol') }}
                </h3>

                <ElRadioGroup
                  v-model="protocol"
                  class="add-nas-protocol-grid"
                >
                  <ElRadio
                    value="smb"
                    border
                    class="add-nas-protocol-card !mr-0"
                  >
                    <div class="add-nas-protocol-card__inner">
                      <span class="add-nas-protocol-card__icon">
                        <component
                          :is="nasMountProtocolIcon('smb')"
                          :size="20"
                          stroke-width="2"
                        />
                      </span>
                      <div class="add-nas-protocol-card__text">
                        <div class="font-semibold">
                          {{ t('repositoriesPage.protocolSmb') }}
                        </div>
                      </div>
                    </div>
                  </ElRadio>
                  <ElRadio
                    value="nfs"
                    border
                    class="add-nas-protocol-card !mr-0"
                  >
                    <div class="add-nas-protocol-card__inner">
                      <span class="add-nas-protocol-card__icon">
                        <component
                          :is="nasMountProtocolIcon('nfs')"
                          :size="20"
                          stroke-width="2"
                        />
                      </span>
                      <div class="add-nas-protocol-card__text">
                        <div class="font-semibold">
                          {{ t('repositoriesPage.protocolNfs') }}
                        </div>
                      </div>
                    </div>
                  </ElRadio>
                </ElRadioGroup>
              </div>
            </section>

            <section
              id="nas-step-auth"
              data-step="0"
              class="fullscreen-form-card fullscreen-form-section add-nas-step-section"
            >
              <div>
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('addNasRepo.titleNasInfo') }}
                </h3>

                <ElForm
                  label-position="top"
                  class="fullscreen-form-el-form add-nas-form"
                >
                  <div
                    v-if="protocol === 'smb'"
                    class="fullscreen-form-grid"
                  >
                    <ElFormItem
                      data-validation-field="smbHost"
                      :error="errors.smbHost"
                      :label="t('addNasRepo.fieldSmbHost')"
                      required
                      class="flex-1"
                    >
                      <ElInput
                        v-model="smbHost"
                        :placeholder="t('addNasRepo.phSmbHost')"
                        @input="clearFieldError('smbHost')"
                      />
                      <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                        {{ t('addNasRepo.hintSmbHost') }}
                      </div>
                    </ElFormItem>
                    <ElFormItem
                      data-validation-field="smbShare"
                      :error="errors.smbShare"
                      :label="t('addNasRepo.fieldSmbShare')"
                      required
                      class="flex-1"
                    >
                      <ElInput
                        v-model="smbShare"
                        :placeholder="t('addNasRepo.phSmbShare')"
                        @input="clearFieldError('smbShare')"
                      />
                      <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                        {{ t('addNasRepo.hintSmbShare') }}
                      </div>
                    </ElFormItem>
                  </div>

                  <div
                    v-if="protocol === 'nfs'"
                    class="fullscreen-form-grid"
                  >
                    <ElFormItem
                      data-validation-field="nfsHost"
                      :error="errors.nfsHost"
                      :label="t('addNasRepo.fieldNfsHost')"
                      required
                      class="flex-1"
                    >
                      <ElInput
                        v-model="nfsHost"
                        :placeholder="t('addNasRepo.phNfsHost')"
                        @input="clearFieldError('nfsHost')"
                      />
                      <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                        {{ t('addNasRepo.hintNfsHost') }}
                      </div>
                    </ElFormItem>
                    <ElFormItem
                      data-validation-field="nfsExport"
                      :error="errors.nfsExport"
                      :label="t('addNasRepo.fieldNfsExport')"
                      required
                      class="flex-1"
                    >
                      <ElInput
                        v-model="nfsExport"
                        :placeholder="t('addNasRepo.phNfsExport')"
                        @input="clearFieldError('nfsExport')"
                      />
                      <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                        {{ t('addNasRepo.hintNfsExport') }}
                      </div>
                    </ElFormItem>
                  </div>

                  <ElFormItem :label="t('addNasRepo.fieldMountOptions')">
                    <ElInput
                      v-model="mountOptions"
                      :placeholder="protocol === 'smb' ? 'vers=3.0' : t('addNasRepo.phMountOptionsNfs')"
                    />
                    <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                      {{ protocol === 'smb'
                        ? 'If SMB auto-negotiation fails, explicitly set the protocol version or authentication option.'
                        : t('addNasRepo.hintMountOptionsNfs') }}
                    </div>
                    <ElAlert
                      v-if="protocol === 'smb'"
                      type="info"
                      :closable="false"
                      show-icon
                      class="mt-2"
                    >
                      <div class="text-xs leading-5 text-[rgb(51_65_85)]">
                        <div>
                          Different NAS devices, SMB servers, and client kernels may support different protocol versions. Copy one example into Mount Options and test:
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

                <template v-if="protocol === 'smb'">
                  <h4 class="fullscreen-form-section__subtitle add-nas-auth-subtitle">
                    <ShieldCheck :size="16" />
                    {{ t('addNasRepo.titleAuth') }}
                  </h4>
                  <ElForm
                    label-position="top"
                    class="fullscreen-form-el-form add-nas-form"
                  >
                    <div class="fullscreen-form-grid">
                      <ElFormItem
                        data-validation-field="smbUsername"
                        :error="errors.smbUsername"
                        :label="t('repositoriesPage.fieldSmbUsername')"
                        required
                        class="flex-1"
                      >
                        <ElInput
                          v-model="smbUsername"
                          :placeholder="t('repositoriesPage.phSmbUsername')"
                          @input="clearFieldError('smbUsername')"
                        />
                        <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                          SMB user used to access the shared directory, e.g. backup_user.
                        </div>
                      </ElFormItem>
                      <ElFormItem
                        data-validation-field="smbPassword"
                        :error="errors.smbPassword"
                        :label="t('repositoriesPage.fieldSmbPassword')"
                        required
                        class="flex-1"
                      >
                        <ElInput
                          v-model="smbPassword"
                          type="password"
                          show-password
                          :placeholder="t('repositoriesPage.phSmbPassword')"
                          @input="clearFieldError('smbPassword')"
                        />
                        <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                          Password for the shared directory; logs and errors are masked.
                        </div>
                      </ElFormItem>
                      <ElFormItem
                        :label="t('repositoriesPage.fieldSmbDomain')"
                        class="flex-1"
                      >
                        <ElInput
                          v-model="smbDomain"
                          :placeholder="t('repositoriesPage.phSmbDomain')"
                        />
                        <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                          Use CORP or WORKGROUP for domain environments; leave empty for local users.
                        </div>
                      </ElFormItem>
                    </div>
                  </ElForm>
                </template>
              </div>
            </section>

            <section
              id="nas-step-proxy"
              data-step="1"
              class="fullscreen-form-card fullscreen-form-section add-nas-step-section"
            >
              <div>
                <div class="fullscreen-form-section__head">
                  <div class="fullscreen-form-section__title-wrap">
                    <h3 class="fullscreen-form-section__title !mb-0">
                      <span class="fullscreen-form-section__indicator" />
                      {{ t('addNasRepo.titleBindProxy') }}
                    </h3>
                  </div>
                  <ElButton
                    class="add-nas-proxy-action"
                    @click="openProxyDeploy"
                  >
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
                      v-for="(item, index) in bindProxyLeadItems"
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
                    <ElForm
                      label-position="top"
                      class="fullscreen-form-el-form add-nas-form"
                    >
                      <ElFormItem
                        data-validation-field="proxyNode"
                        :error="errors.proxyNode"
                        required
                        class="add-nas-bind-form-item"
                      >
                        <template #label>
                          {{ t('addNasRepo.fieldSourceProxyNode') }}
                        </template>
                        <div class="add-nas-select-row">
                          <ElSelect
                            v-model="proxyNodeId"
                            class="add-nas-select-row__select"
                            clearable
                            filterable
                            :placeholder="t('addNasRepo.phSourceProxyNode')"
                            @clear="clearProxySelection(); clearFieldError('proxyNode')"
                            @change="clearFieldError('proxyNode')"
                          >
                            <ElOption
                              v-for="n in availableProxyNodes"
                              :key="n.id"
                              :value="n.id"
                              :label="n.ip_address ? `${n.name} (${n.ip_address})` : n.name"
                            />
                            <ElOption
                              :value="0"
                              :label="t('addNasRepo.optionNoProxy')"
                            />
                          </ElSelect>
                          <ElButton
                            class="hfl-refresh-button add-nas-select-row__refresh"
                            :title="t('addNasRepo.proxyRefresh')"
                            :aria-label="t('addNasRepo.proxyRefresh')"
                            :disabled="proxyNodesRefreshing"
                            @click="refreshProxyNodesManually"
                          >
                            <RefreshCw
                              :size="16"
                              :class="{ 'is-spinning': proxyNodesRefreshing }"
                            />
                          </ElButton>
                        </div>
                        <div
                          v-if="hasBoundProxy"
                          class="mt-2 text-xs text-[rgb(100_116_139)]"
                        >
                          {{ t('addNasRepo.hintProxySelected') }}
                        </div>
                        <div
                          v-else-if="!hasProxyDecision"
                          class="mt-2 text-xs text-[rgb(100_116_139)]"
                        >
                          {{ t('addNasRepo.hintProxyUndecided') }}
                        </div>
                        <ElAlert
                          v-else
                          type="warning"
                          :closable="false"
                          show-icon
                          class="add-nas-direct-warning"
                        >
                          <ol class="add-nas-proxy-alert__list add-nas-direct-warning__list">
                            <li
                              v-for="(item, index) in directAccessRiskItems"
                              :key="item"
                              class="add-nas-proxy-alert__item"
                            >
                              <span class="add-nas-proxy-alert__index">{{ index + 1 }}</span>
                              <span class="add-nas-proxy-alert__text">{{ item }}</span>
                            </li>
                          </ol>
                        </ElAlert>
                      </ElFormItem>
                    </ElForm>

                    <div class="add-nas-proxy-benefits">
                      <div class="add-nas-proxy-benefit">
                        <span class="add-nas-proxy-benefit__dot" />
                        {{ t('addNasRepo.proxyBenefitAvoidMountFailure') }}
                      </div>
                      <div class="add-nas-proxy-benefit">
                        <span class="add-nas-proxy-benefit__dot" />
                        {{ t('addNasRepo.proxyBenefitSharedRepo') }}
                      </div>
                    </div>
                  </div>

                  <NasProxyTopology
                    :direct="isDirectNasAccess"
                    :source-labels="topologySourceLabels"
                  />
                </div>
              </div>
            </section>

            <section
              id="nas-step-repo"
              data-step="2"
              class="fullscreen-form-card fullscreen-form-section add-nas-step-section"
            >
              <div>
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('repositoriesPage.stepRepo') }}
                </h3>
                <ElForm
                  label-position="top"
                  class="fullscreen-form-el-form add-nas-form"
                >
                  <ElFormItem
                    data-validation-field="repoName"
                    :error="errors.repoName"
                    :label="t('repositoriesPage.fieldRepoName')"
                    required
                  >
                    <ElInput
                      v-model="repoName"
                      :placeholder="t('repositoriesPage.phRepoName')"
                      @input="clearFieldError('repoName')"
                    />
                    <div class="mt-1 text-xs text-[rgb(100_116_139)]">
                      Display name used in repository lists and backup configs, e.g. Production NAS backup.
                    </div>
                  </ElFormItem>
                  <div class="fullscreen-form-grid">
                    <div class="fullscreen-form-field repository-quota-field">
                      <label class="fullscreen-form-field__label repository-quota-head">{{ t('repositoriesPage.fieldQuota') }}</label>
                      <div class="repository-quota-control">
                        <div class="repository-quota-number repository-quota-input repository-quota-split-input repository-quota-split-input--add">
                          <ElInputNumber
                            v-model="quota"
                            class="repository-quota-number__input"
                            :placeholder="t('repositoriesPage.phQuota')"
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
                        {{ t('repositoriesPage.hintQuota') }}
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
                            :disabled="!enableQuotaAlert"
                            controls-position="right"
                            @change="clearFieldError('quotaThreshold')"
                          />
                          <div class="repository-quota-number__suffix">
                            %
                          </div>
                        </div>
                      </div>
                      <p class="fullscreen-form-field__hint">
                        {{ t('repositoriesPage.hintQuotaAlertThreshold') }}
                      </p>
                      <p
                        v-if="errors.quotaThreshold"
                        class="el-form-item__error"
                      >
                        {{ errors.quotaThreshold }}
                      </p>
                    </div>
                  </div>
                </ElForm>
              </div>
            </section>
          </div>

          <div class="fullscreen-form-footer fullscreen-form-action-footer add-nas-footer">
            <div class="flex gap-2">
              <ElButton @click="handleBack">
                {{ t('repositoriesPage.btnCancel') }}
              </ElButton>
              <ElButton
                type="primary"
                :loading="busy"
                :disabled="busy"
                @click="onSubmit"
              >
                {{ submitButtonLabel }}
              </ElButton>
            </div>
          </div>
        </div>

        <aside
          v-if="!embedded"
          class="fullscreen-form-sidebar add-form-preview-sidebar"
        >
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon">
                <component
                  :is="nasMountProtocolIcon(protocol)"
                  class="add-form-preview-header__icon-lucide"
                  :size="28"
                />
              </div>
              <div class="add-form-preview-header__info">
                <h4 class="add-form-preview-header__name">
                  {{ repoName || t('addS3Repo.previewUnnamed') }}
                </h4>
                <p class="add-form-preview-header__type">
                  {{ protocolLabel }}
                </p>
              </div>
            </div>

            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('addS3Repo.previewBasicInfo') }}
                </h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldRepoName') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !repoName }"
                  >
                    {{ repoName || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldProtocol') }}</span>
                  <span class="add-form-preview-row__value add-form-preview-row__value--primary">{{ protocolLabel }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addNasRepo.fieldSourceProxyNode') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !selectedProxyNodeName }"
                  >
                    {{ proxyPreviewLabel }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldAccessPath') }}</span>
                  <span class="add-form-preview-row__value">{{ accessPathLabel }}</span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldQuota') }}</span>
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

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('addS3Repo.previewConnection') }}
                </h5>
                <div
                  v-if="protocol === 'smb'"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ t('addNasRepo.fieldSmbHost') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !smbHost }"
                  >
                    {{ smbHost || '—' }}
                  </span>
                </div>
                <div
                  v-if="protocol === 'smb'"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ t('addNasRepo.fieldSmbShare') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !smbShare }"
                  >
                    {{ smbShare || '—' }}
                  </span>
                </div>
                <div
                  v-if="protocol === 'smb'"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldSmbUsername') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !smbUsername }"
                  >
                    {{ smbUsername || '—' }}
                  </span>
                </div>
                <div
                  v-if="protocol === 'smb'"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ t('repositoriesPage.fieldSmbDomain') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !smbDomain }"
                  >
                    {{ smbDomain || '—' }}
                  </span>
                </div>
                <div
                  v-if="protocol === 'nfs'"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ t('addNasRepo.fieldNfsHost') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !nfsHost }"
                  >
                    {{ nfsHost || '—' }}
                  </span>
                </div>
                <div
                  v-if="protocol === 'nfs'"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ t('addNasRepo.fieldNfsExport') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !nfsExport }"
                  >
                    {{ nfsExport || '—' }}
                  </span>
                </div>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('addNasRepo.fieldMountOptions') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--empty': !mountOptions }"
                  >
                    {{ mountOptions || '—' }}
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
.add-nas-main {
  box-sizing: border-box;
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

.add-nas-form {
  margin-top: 4px;
}

.add-nas-auth-subtitle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: max-content;
  max-width: 100%;
  color: rgb(71 85 105);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
}

.add-nas-auth-subtitle svg {
  flex: 0 0 auto;
}

.add-nas-bind-form-item :deep(.el-form-item__content) {
  width: 100%;
}

.add-nas-direct-warning {
  width: 100%;
  margin-top: 8px;
}

.add-nas-direct-warning :deep(.el-alert__title) {
  line-height: 1.5;
}

.add-nas-direct-warning :deep(.el-alert__content) {
  min-width: 0;
}

.add-nas-direct-warning__list {
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
  width: 100%;
  max-width: none;
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

.add-nas-proxy-alert :deep(.el-alert__icon) {
  align-self: flex-start;
  margin-top: 2px;
  color: var(--color-warning);
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

.add-nas-path-card {
  display: grid;
  grid-template-columns: 74px 42px 88px 34px 72px;
  align-items: center;
  min-height: 150px;
  padding: 18px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(248, 250, 252, 0.9) 100%);
}

.add-nas-path-card--direct {
  display: flex;
  align-items: center;
  padding: 16px 18px;
  border-color: rgba(226, 232, 240, 0.95);
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.94) 0%, rgba(241, 245, 249, 0.72) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.82);
}

.add-nas-path-card__agents {
  display: grid;
  gap: 10px;
}

.add-nas-path-card__agents span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  border-radius: 6px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  background: #fff;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 600;
}

.add-nas-path-card__source {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  min-width: 72px;
  border-radius: 6px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  background: #fff;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 600;
}

.add-nas-path-card__join {
  position: relative;
  height: 96px;
}

.add-nas-path-card__join::before {
  content: '';
  position: absolute;
  top: 14px;
  bottom: 14px;
  left: 12px;
  width: 1px;
  background: rgb(148 163 184);
}

.add-nas-path-card__join::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 12px;
  right: 0;
  height: 1px;
  background: rgb(148 163 184);
}

.add-nas-path-card__line {
  height: 1px;
  background: rgb(148 163 184);
}

.add-nas-path-card__direct-rows {
  display: grid;
  width: 100%;
  gap: 12px;
  min-width: 0;
}

.add-nas-path-card__direct-row {
  display: grid;
  grid-template-columns: 82px minmax(72px, 1fr) 82px;
  align-items: center;
  gap: 12px;
}

.add-nas-path-card__direct-line {
  position: relative;
  height: 2px;
  min-width: 72px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(148, 163, 184, 0.28), rgba(69, 122, 176, 0.62), rgba(22, 163, 74, 0.42));
}

.add-nas-path-card__direct-line::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.5);
  transform: translateY(-50%);
}

.add-nas-path-card__direct-line::after {
  content: '';
  position: absolute;
  top: 50%;
  right: -1px;
  width: 6px;
  height: 6px;
  border-top: 1px solid rgba(22, 163, 74, 0.72);
  border-right: 1px solid rgba(22, 163, 74, 0.72);
  transform: translateY(-50%) rotate(45deg);
}

.add-nas-path-card--direct .add-nas-path-card__source,
.add-nas-path-card--direct .add-nas-path-card__node {
  width: 100%;
  height: 32px;
  border-radius: 6px;
  box-sizing: border-box;
}

.add-nas-path-card__node {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 38px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
}

.add-nas-path-card__node--proxy {
  border: 1px solid rgba(37, 99, 235, 0.28);
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
}

.add-nas-path-card__node--nas {
  border: 1px solid rgba(22, 163, 74, 0.26);
  background: rgba(240, 253, 244, 0.92);
  color: rgb(21 128 61);
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
  border-color: var(--color-border, #e9e9ef) !important;
  background: var(--color-card-bg, #fff);
  box-shadow: none;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}

.add-nas-protocol-card:hover {
  border-color: color-mix(in srgb, var(--color-primary, #6d5ef6) 38%, var(--color-border, #e9e9ef)) !important;
  background: #fff;
  box-shadow: 0 8px 18px -16px rgba(28, 28, 38, 0.35);
  transform: translateY(-1px);
}

.add-nas-protocol-card.is-checked {
  border-color: var(--color-primary, #6d5ef6) !important;
  background: var(--color-primary-light, #f2f0fe);
  color: var(--color-primary-hover, #5546d8);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary, #6d5ef6) 7%, transparent);
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
  border: 1px solid transparent;
  background: var(--color-primary-light, #f2f0fe);
  color: var(--color-primary-hover, #5546d8);
  transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
}

.add-nas-protocol-card:hover .add-nas-protocol-card__icon,
.add-nas-protocol-card.is-checked .add-nas-protocol-card__icon {
  border-color: var(--color-primary-disabled-bg, #dcd5fb);
  background: #fff;
  color: var(--color-primary-hover, #5546d8);
}

.add-nas-protocol-card__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
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

.add-nas-review__desc :deep(.el-descriptions__label) {
  font-weight: 500;
}

@media (max-width: 768px) {
  .resource-add-fullscreen .add-nas-protocol-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }

  .resource-add-fullscreen .add-nas-protocol-card {
    min-height: 52px;
    padding: 8px 12px !important;
  }

  .resource-add-fullscreen .add-nas-protocol-card :deep(.el-radio__label) {
    min-width: 0;
    padding-left: 8px;
  }

  .resource-add-fullscreen .add-nas-protocol-card__inner {
    min-width: 0;
    gap: 0;
  }

  .resource-add-fullscreen .add-nas-protocol-card__icon {
    display: none;
  }

  .resource-add-fullscreen .add-nas-protocol-card__text {
    min-width: 0;
    font-size: 14px;
    line-height: 1.35;
  }

  .add-nas-proxy-action {
    width: 100%;
  }

  .add-nas-proxy-layout {
    grid-template-columns: 1fr;
  }

  .add-nas-path-card {
    grid-template-columns: 68px 34px 80px 28px 64px;
    padding: 14px;
    overflow-x: auto;
  }

  .add-nas-path-card--direct {
    display: flex;
  }

  .add-nas-path-card__direct-row {
    grid-template-columns: 78px minmax(64px, 1fr) 78px;
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
