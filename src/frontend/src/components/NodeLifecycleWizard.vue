<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { I18nT, useI18n } from 'vue-i18n'
import { Copy, Check, TriangleAlert, RefreshCw, ChevronDown, Info, Cpu, MemoryStick, HardDrive } from 'lucide-vue-next'
import AgentPlatformBrandIcon from './agent-deploy/AgentPlatformBrandIcon.vue'
import UbuntuBrandIcon from './agent-deploy/UbuntuBrandIcon.vue'
import {
  buildLocalServiceCommand,
  buildLocalUninstallCommand,
  buildLocalUpgradeCommand,
  defaultPackagePath,
  installPathsSummary,
  isLinuxOnlyRole,
  roleDeployNotes,
  roleSupportedOnOs,
  type NodeLifecycleTab,
} from '../lib/nodeInstallCommands'
import {
  fetchNodeMaintenanceRelease,
  issueEnrollmentInstall,
  issueGatewayEnrollmentInstall,
  issuePlatformGatewayEnrollmentInstall,
  revokeEnrollmentToken,
  revokePlatformGatewayEnrollment,
  type EnrollmentOs,
} from '../lib/nodeApi'
import { apiErrorMessage } from '../lib/api'
import type { NodeInstallationMode, NodeRole } from '../types/node'

const props = withDefaults(
  defineProps<{
    orgKey: string
    nodeId?: number | null
    role: NodeRole
    os: EnrollmentOs
    roleLocked?: boolean
    showRolePicker?: boolean
    gatewayScope?: 'user' | 'platform'
    initialTab?: NodeLifecycleTab
    /** Hide upgrade/uninstall/service tabs (install-only embed). */
    installOnly?: boolean
    /** Show host-side recovery commands only (upgrade/uninstall/service). */
    maintenanceOnly?: boolean
    initialServiceAction?: 'status' | 'start' | 'stop' | 'restart'
    /** Require an explicit operator action before issuing an enrollment token. */
    generateOnDemand?: boolean
    installationMode?: NodeInstallationMode
  }>(),
  {
    nodeId: null,
    roleLocked: false,
    showRolePicker: false,
    gatewayScope: 'user',
    initialTab: 'install',
    installOnly: false,
    maintenanceOnly: false,
    initialServiceAction: 'status',
    generateOnDemand: false,
    installationMode: undefined,
  },
)

const emit = defineEmits<{
  'update:os': [EnrollmentOs]
  'update:role': [NodeRole]
  copy: [string]
  enrollmentIssued: [{ tokenId: number; expiresAt: string | null }]
}>()

const { t } = useI18n()

const activeTab = ref<NodeLifecycleTab>(
  props.maintenanceOnly && props.initialTab === 'install' ? 'upgrade' : props.initialTab,
)
const installCommand = ref('')
const upgradeCommand = ref('')
const uninstallCommand = ref('')
const serviceCommand = ref('')
const loading = ref(false)
const releaseVersion = ref('')
const upgradeError = ref('')
const copied = ref(false)
const installGenerated = ref(false)
const purgeAll = ref(false)
const serviceAction = ref<'status' | 'start' | 'stop' | 'restart'>(props.initialServiceAction)
const defaultInstallationModeForOs = (os: EnrollmentOs): NodeInstallationMode => (
  os === 'linux' ? 'user_continuous' : 'user'
)
const selectedInstallationMode = ref<NodeInstallationMode>(
  props.installationMode ?? defaultInstallationModeForOs(props.os),
)
const installationModeTouched = ref(props.installationMode != null)
const effectiveInstallationMode = computed<NodeInstallationMode>(() => (
  props.role === 'agent' ? selectedInstallationMode.value : 'system'
))
const supportOpen = ref(false)
const enrollmentTokenId = ref<number | null>(null)
const enrollmentTokenIsPlatform = ref(false)
const enrollmentExpiresAt = ref<string | null>(null)
const tokenClock = ref(Date.now())
let tokenStatusTimer: ReturnType<typeof setInterval> | null = null

const LINUX_DISTROS = {
  deb: ['Ubuntu', 'Debian'],
  rpm: ['RHEL', 'CentOS Stream', 'Rocky Linux', 'AlmaLinux', 'Fedora', 'openSUSE Leap'],
  cloud: ['Amazon Linux', 'Oracle Linux', 'Arch Linux'],
} as const

const visibleTabs = computed((): NodeLifecycleTab[] => {
  if (props.installOnly) return ['install']
  if (props.maintenanceOnly) return ['upgrade', 'uninstall', 'service']
  return ['install', 'upgrade', 'uninstall', 'service']
})

const tokenIsUsable = computed(() => {
  if (!installGenerated.value) return false
  if (!enrollmentExpiresAt.value) return true
  return new Date(enrollmentExpiresAt.value).getTime() > tokenClock.value
})

const tokenValidityLabel = computed(() => {
  if (!installGenerated.value) return ''
  if (!tokenIsUsable.value) {
    return t('nodeLifecycle.installCommandExpired')
  }
  if (!enrollmentExpiresAt.value) return t('nodeLifecycle.installCommandActive')
  const seconds = Math.max(0, Math.floor((new Date(enrollmentExpiresAt.value).getTime() - tokenClock.value) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return t('nodeLifecycle.installCommandValidFor', { hours, minutes })
})

const localCommandWarning = computed(() => {
  if (activeTab.value === 'install') {
    return installGenerated.value && /(?:127\.0\.0\.1|localhost)/i.test(installCommand.value)
  }
  return activeTab.value === 'upgrade'
    && /(?:127\.0\.0\.1|localhost)/i.test(upgradeCommand.value)
})

const localCommandWarningText = computed(() => (
  activeTab.value === 'upgrade'
    ? t('nodeLifecycle.localUpgradeCommandWarning')
    : t('nodeLifecycle.localInstallCommandWarning')
))

const isUbuntuHostDeploy = computed(
  () => props.installOnly && (props.role === 'proxy' || props.role === 'gateway'),
)

const proxyReqCards = computed(() => [
  {
    key: 'os',
    kind: 'ubuntu' as const,
    title: t('nodesDeploy.proxyDeployUbuntuTitle'),
    sub: t('nodesDeploy.proxyDeployUbuntuMeta'),
  },
  {
    key: 'cpu',
    kind: 'icon' as const,
    icon: Cpu,
    title: t('nodesDeploy.proxyReqCpu'),
    sub: t('nodesDeploy.proxyReqCpuSub'),
  },
  {
    key: 'mem',
    kind: 'icon' as const,
    icon: MemoryStick,
    title: t('nodesDeploy.proxyReqMem'),
    sub: t('nodesDeploy.proxyReqMemSub'),
  },
  {
    key: 'disk',
    kind: 'icon' as const,
    icon: HardDrive,
    title: t('nodesDeploy.proxyReqDisk'),
    sub: props.role === 'gateway'
      ? t('nodesDeploy.gatewayReqDiskSub')
      : t('nodesDeploy.proxyReqDiskSub'),
  },
])

const osDisabled = computed(() => ({
  windows: isLinuxOnlyRole(props.role),
  macos: isLinuxOnlyRole(props.role),
}))

const roleDisabled = computed(() => ({
  proxy: props.os !== 'linux',
  gateway: props.os !== 'linux',
}))

const linuxOnlyRoleHint = computed(() =>
  isLinuxOnlyRole(props.role) ? t('nodeLifecycle.linuxOnlyRoleHint') : '',
)

let generation = 0
let copiedTimer: ReturnType<typeof setTimeout> | undefined

const roleOptions = computed(() => [
  { value: 'agent' as NodeRole, title: t('nodesDeploy.roleAgentTitle'), desc: t('nodesDeploy.roleAgentDesc') },
  { value: 'proxy' as NodeRole, title: t('nodesDeploy.roleProxyTitle'), desc: t('nodesDeploy.roleProxyDesc') },
  { value: 'gateway' as NodeRole, title: t('nodesDeploy.roleGatewayTitle'), desc: t('nodesDeploy.roleGatewayDesc') },
])

const roleLabel = computed(() => {
  if (props.role === 'proxy') return t('nodesPage.roleProxy')
  if (props.role === 'gateway') return t('nodesPage.roleGateway')
  return t('nodesPage.roleAgent')
})

const paths = computed(() => installPathsSummary(
  props.os,
  props.role,
  effectiveInstallationMode.value,
))

const displayCommand = computed(() => {
  switch (activeTab.value) {
    case 'upgrade':
      return upgradeCommand.value || (loading.value ? t('nodeLifecycle.upgradeLoading') : '')
    case 'uninstall':
      return uninstallCommand.value
    case 'service':
      return serviceCommand.value
    default:
      if (installCommand.value) return installCommand.value
      return props.generateOnDemand
        ? t('nodeLifecycle.generateInstallCommandHint')
        : t('nodesDeploy.scriptLoading')
  }
})

const tabHint = computed(() => {
  if (props.role === 'gateway' && activeTab.value !== 'install') {
    return t(`nodeLifecycle.gatewayTabHint.${activeTab.value}`)
  }
  return t(`nodeLifecycle.tabHint.${activeTab.value}`)
})

const footnote = computed(() => {
  if (props.role === 'gateway' && activeTab.value !== 'install') {
    return t(`nodeLifecycle.gatewayFootnote.${activeTab.value}`)
  }
  return t(`nodeLifecycle.footnote.${activeTab.value}`)
})

const osPickerOptions = computed(() => [
  { value: 'linux' as EnrollmentOs, label: t('nodesDeploy.osLinux'), meta: t('nodeLifecycle.osMetaLinux') },
  { value: 'windows' as EnrollmentOs, label: t('nodesDeploy.osWindows'), meta: t('nodeLifecycle.osMetaWindows') },
  { value: 'macos' as EnrollmentOs, label: t('nodesDeploy.osMacos'), meta: t('nodeLifecycle.osMetaMacos') },
])

// Keep service-manager details internal. The available choices describe the
// protection result and lifecycle boundary; the installer selects the native
// service mechanism for the chosen operating system.
const installationModeOptions = computed(() => [
  {
    value: 'user' as NodeInstallationMode,
    title: t('nodeLifecycle.installationModeUser'),
    description: t('nodeLifecycle.installationModeUserDescription'),
    scope: t('nodeLifecycle.installationModeUserScope'),
    runtime: t('nodeLifecycle.installationModeUserRuntime'),
    permission: t('nodeLifecycle.installationModeUserPermission'),
    recommendation: t(`nodeLifecycle.installationModeUserRecommendation.${props.os}`),
    recommended: defaultInstallationModeForOs(props.os) === 'user',
  },
  {
    value: 'user_continuous' as NodeInstallationMode,
    title: t('nodeLifecycle.installationModeUserContinuous'),
    description: t('nodeLifecycle.installationModeUserContinuousDescription'),
    scope: t('nodeLifecycle.installationModeUserContinuousScope'),
    runtime: t('nodeLifecycle.installationModeUserContinuousRuntime'),
    permission: t('nodeLifecycle.installationModeUserContinuousPermission'),
    recommendation: t(`nodeLifecycle.installationModeUserContinuousRecommendation.${props.os}`),
    recommended: defaultInstallationModeForOs(props.os) === 'user_continuous',
  },
  {
    value: 'account' as NodeInstallationMode,
    title: t('nodeLifecycle.installationModeAccount'),
    description: t('nodeLifecycle.installationModeAccountDescription'),
    scope: t('nodeLifecycle.installationModeAccountScope'),
    runtime: t('nodeLifecycle.installationModeAccountRuntime'),
    permission: t('nodeLifecycle.installationModeAccountPermission'),
    recommendation: t(`nodeLifecycle.installationModeAccountRecommendation.${props.os}`),
    recommended: false,
  },
  {
    value: 'system' as NodeInstallationMode,
    title: t('nodeLifecycle.installationModeSystem'),
    description: t('nodeLifecycle.installationModeSystemDescription'),
    scope: t('nodeLifecycle.installationModeSystemScope'),
    runtime: t('nodeLifecycle.installationModeSystemRuntime'),
    permission: t('nodeLifecycle.installationModeSystemPermission'),
    recommendation: t(`nodeLifecycle.installationModeSystemRecommendation.${props.os}`),
    recommended: defaultInstallationModeForOs(props.os) === 'system',
  },
].filter((option) => option.value !== 'user_continuous' || props.os === 'linux'))

const installLeadKey = computed(() => {
  if (effectiveInstallationMode.value === 'user' || effectiveInstallationMode.value === 'user_continuous') return 'nodeLifecycle.installLeadUser'
  if (props.os === 'windows') return 'nodeLifecycle.installLeadWindows'
  if (props.os === 'macos') return 'nodeLifecycle.installLeadMacos'
  return 'nodeLifecycle.installLeadLinux'
})

const installFlowRegisterText = computed(() => {
  if (props.role === 'proxy') return t('nodeLifecycle.installFlowRegisterProxy')
  if (props.role === 'gateway') {
    return props.gatewayScope === 'platform'
      ? t('nodeLifecycle.installFlowRegisterPublicGateway')
      : t('nodeLifecycle.installFlowRegisterPrivateGateway')
  }
  return t('nodeLifecycle.installFlowRegister')
})

const consoleBarTitle = computed(() => {
  if (props.installOnly) {
    if (props.os === 'windows') return t('nodeLifecycle.consolePowerShell')
    if (props.os === 'macos') return t('nodeLifecycle.consoleZsh')
    return t('nodeLifecycle.consoleBash')
  }
  return t('nodeLifecycle.consoleTitle', { role: roleLabel.value })
})

const viewSupportedLabel = computed(() => {
  if (props.os === 'windows') return t('nodeLifecycle.viewSupportedWindows')
  if (props.os === 'macos') return t('nodeLifecycle.viewSupportedMacos')
  return t('nodeLifecycle.viewSupportedLinux')
})

const roleNote = computed(() => {
  const keys = roleDeployNotes(props.role)
  if (!keys.length) return ''
  return keys.map((k) => t(`nodesDeploy.${k}`)).join(' ')
})

function selectOs(next: EnrollmentOs) {
  if (isLinuxOnlyRole(props.role) && next !== 'linux') return
  emit('update:os', next)
}

function selectRole(next: NodeRole) {
  if (props.roleLocked) return
  emit('update:role', next)
  if (isLinuxOnlyRole(next) && props.os !== 'linux') {
    emit('update:os', 'linux')
  }
}

async function refreshInstallCommand(gen: number) {
  if (!props.orgKey || !roleSupportedOnOs(props.role, props.os)) {
    installCommand.value = roleSupportedOnOs(props.role, props.os)
      ? ''
      : t('nodeLifecycle.linuxOnlyRoleBlocked')
    return
  }
  loading.value = true
  installCommand.value = ''
  const platformEnrollment = props.role === 'gateway' && props.gatewayScope === 'platform'
  try {
    const issued =
      props.role === 'gateway' && props.os === 'linux'
        ? platformEnrollment
          ? await issuePlatformGatewayEnrollmentInstall({
              note: 'deploy:platform-gateway',
            })
          : await issueGatewayEnrollmentInstall({ note: `deploy:${props.role}`, orgKey: props.orgKey })
        : await issueEnrollmentInstall({
            role: props.role,
            os: props.os,
            installationMode: effectiveInstallationMode.value,
            note: `deploy:${props.role}`,
          })
    if (gen !== generation) {
      await revokeIssuedEnrollment(issued.tokenId, platformEnrollment)
      return
    }
    installCommand.value = issued.command
    installGenerated.value = true
    enrollmentTokenId.value = issued.tokenId
    enrollmentTokenIsPlatform.value = platformEnrollment
    enrollmentExpiresAt.value = issued.expiresAt
    emit('enrollmentIssued', {
      tokenId: issued.tokenId,
      expiresAt: 'expiresAt' in issued ? issued.expiresAt : null,
    })
  } catch (e) {
    if (gen === generation) {
      installCommand.value = apiErrorMessage(e, t('nodesDeploy.scriptLoadFailed'))
      installGenerated.value = false
    }
  } finally {
    if (gen === generation) loading.value = false
  }
}

async function revokeIssuedEnrollment(tokenId: number, platformEnrollment: boolean) {
  if (platformEnrollment) {
    await revokePlatformGatewayEnrollment(tokenId).catch(() => undefined)
  } else {
    await revokeEnrollmentToken(tokenId).catch(() => undefined)
  }
}

async function refreshUpgradeCommand(gen: number) {
  const platformGateway = props.role === 'gateway' && props.gatewayScope === 'platform'
  if (props.nodeId == null) {
    upgradeCommand.value = ''
    upgradeError.value = t('nodeLifecycle.upgradeCommandUnavailable')
    return
  }
  loading.value = true
  upgradeCommand.value = ''
  upgradeError.value = ''
  try {
    const release = await fetchNodeMaintenanceRelease({
      nodeId: props.nodeId,
      scope: platformGateway ? 'platform' : 'tenant',
    })
    if (gen !== generation) return
    releaseVersion.value = release.version
    upgradeCommand.value = buildLocalUpgradeCommand(
      props.os,
      defaultPackagePath(
        props.os,
        release.version,
        release.arch === 'arm64' ? 'arm64' : 'amd64',
      ),
      true,
      release.download_url,
      props.role,
      release.tls_verify !== false,
      '',
      release.arch === 'arm64' ? 'arm64' : 'amd64',
      effectiveInstallationMode.value,
    )
    if (!upgradeCommand.value) throw new Error(t('nodeLifecycle.upgradeCommandUnavailable'))
  } catch (error) {
    if (gen === generation) {
      releaseVersion.value = ''
      upgradeCommand.value = ''
      upgradeError.value = apiErrorMessage(error, t('nodeLifecycle.upgradeCommandUnavailable'))
    }
  } finally {
    if (gen === generation) loading.value = false
  }
}

function refreshStaticCommands() {
  uninstallCommand.value = buildLocalUninstallCommand(
    props.os,
    purgeAll.value,
    props.role,
    effectiveInstallationMode.value,
  )
  serviceCommand.value = buildLocalServiceCommand(
    props.os,
    serviceAction.value,
    props.role,
    effectiveInstallationMode.value,
  )
}

function refreshAll() {
  const gen = ++generation
  refreshStaticCommands()
  if (activeTab.value === 'install' && !props.generateOnDemand) {
    void refreshInstallCommand(gen)
  } else if (activeTab.value === 'upgrade') {
    void refreshUpgradeCommand(gen)
  }
}

watch(
  () => [props.orgKey, props.nodeId, props.role, props.os, props.gatewayScope] as const,
  () => {
    if (isLinuxOnlyRole(props.role) && props.os !== 'linux') {
      emit('update:os', 'linux')
    }
    const staleTokenId = enrollmentTokenId.value
    const staleTokenIsPlatform = enrollmentTokenIsPlatform.value
    clearInstallCommand()
    if (staleTokenId) {
      void revokeIssuedEnrollment(staleTokenId, staleTokenIsPlatform)
    }
    refreshAll()
  },
  { immediate: true },
)

watch(
  () => props.installationMode,
  (mode) => {
    if (mode) {
      selectedInstallationMode.value = mode
      installationModeTouched.value = true
    }
  },
)

watch(
  () => props.os,
  (os) => {
    if (os !== 'linux' && selectedInstallationMode.value === 'user_continuous') {
      selectedInstallationMode.value = defaultInstallationModeForOs(os)
      installationModeTouched.value = false
      return
    }
    if (!installationModeTouched.value && props.role === 'agent') {
      selectedInstallationMode.value = defaultInstallationModeForOs(os)
    }
  },
)

watch(selectedInstallationMode, () => {
  const staleTokenId = enrollmentTokenId.value
  const staleTokenIsPlatform = enrollmentTokenIsPlatform.value
  clearInstallCommand()
  if (staleTokenId) {
    void revokeIssuedEnrollment(staleTokenId, staleTokenIsPlatform)
  }
  refreshAll()
})

watch(
  () => props.initialTab,
  (tab) => {
    if (tab) activeTab.value = props.maintenanceOnly && tab === 'install' ? 'upgrade' : tab
  },
)

watch(
  () => props.initialServiceAction,
  (action) => {
    serviceAction.value = action
  },
)

watch(activeTab, (tab) => {
  const gen = ++generation
  if (tab === 'install' && !props.generateOnDemand) void refreshInstallCommand(gen)
  else if (tab === 'upgrade') void refreshUpgradeCommand(gen)
})

watch([purgeAll, serviceAction, () => props.os], () => refreshStaticCommands())

watch(
  () => props.os,
  () => {
    supportOpen.value = false
  },
)

function onOsCardKeydown(event: KeyboardEvent, next: EnrollmentOs) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    selectOs(next)
  }
}

function onCopy() {
  const text = displayCommand.value
  if (!text || loading.value) return
  emit('copy', text)
  copied.value = true
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => {
    copied.value = false
  }, 2000)
}

function generateInstallCommand() {
  if (loading.value || installGenerated.value) return
  void refreshInstallCommand(++generation)
}

function clearInstallCommand() {
  generation += 1
  installGenerated.value = false
  installCommand.value = ''
  copied.value = false
  enrollmentTokenId.value = null
  enrollmentTokenIsPlatform.value = false
  enrollmentExpiresAt.value = null
}

onMounted(() => {
  tokenStatusTimer = setInterval(() => {
    tokenClock.value = Date.now()
  }, 60_000)
})

onBeforeUnmount(() => {
  if (tokenStatusTimer) clearInterval(tokenStatusTimer)
})

defineExpose({ clearInstallCommand })
</script>

<template>
  <div
    class="node-lifecycle-wizard agent-install-wizard"
    :class="{
      'agent-install-wizard--source-host': installOnly,
      'agent-install-wizard--ubuntu-host': isUbuntuHostDeploy,
      'agent-install-wizard--maintenance': maintenanceOnly,
    }"
  >
    <ElAlert
      v-if="!orgKey && gatewayScope !== 'platform'"
      type="warning"
      :closable="false"
      show-icon
      class="source-deploy-fullscreen__alert"
    >
      {{ t('nodesDeploy.missingOrgBanner') }}
    </ElAlert>

    <div class="fullscreen-form-step-stack">
      <div
        v-if="showRolePicker && !roleLocked"
        class="fullscreen-form-card"
      >
        <section class="fullscreen-form-section">
          <h3 class="fullscreen-form-section__title">
            <span class="fullscreen-form-section__indicator" />
            {{ t('nodesDeploy.step2') }}
          </h3>
          <ElRadioGroup
            :model-value="role"
            class="deploy-role-grid"
            @update:model-value="selectRole"
          >
            <ElRadio
              v-for="opt in roleOptions"
              :key="opt.value"
              :value="opt.value"
              :disabled="roleDisabled[opt.value as 'proxy' | 'gateway']"
              border
              class="deploy-role-option !mr-0"
            >
              <div>
                <div class="deploy-role-option__title">
                  {{ opt.title }}
                </div>
                <div class="deploy-role-option__desc">
                  {{ opt.desc }}
                </div>
              </div>
            </ElRadio>
          </ElRadioGroup>
        </section>
      </div>

      <div
        v-if="!maintenanceOnly"
        class="fullscreen-form-card"
        :class="{ 'agent-install-wizard__platform-card': installOnly }"
      >
        <section
          class="fullscreen-form-section"
          :class="{ 'agent-install-wizard__platform-section': installOnly }"
        >
          <div
            v-if="installOnly"
            class="agent-install-wizard__platform-head"
          >
            <h3 class="fullscreen-form-section__title">
              <span class="fullscreen-form-section__indicator" />
              {{ isUbuntuHostDeploy ? t('nodesDeploy.proxyReqTitle') : t('nodeLifecycle.osStep') }}
            </h3>
          </div>
          <h3
            v-else
            class="fullscreen-form-section__title"
          >
            <span class="fullscreen-form-section__indicator" />
            {{ isUbuntuHostDeploy ? t('nodesDeploy.proxyReqTitle') : t('nodeLifecycle.osStep') }}
          </h3>

          <template v-if="isUbuntuHostDeploy">
            <div
              class="proxy-req-grid"
              role="list"
              :aria-label="t('nodesDeploy.proxyReqTitle')"
            >
              <div
                v-for="card in proxyReqCards"
                :key="card.key"
                class="proxy-req-card"
                role="listitem"
              >
                <div
                  class="proxy-req-card__icon-wrap"
                  aria-hidden="true"
                >
                  <UbuntuBrandIcon
                    v-if="card.kind === 'ubuntu'"
                    class="proxy-req-card__brand proxy-req-card__brand--ubuntu"
                  />
                  <component
                    :is="card.icon"
                    v-else
                    class="proxy-req-card__brand proxy-req-card__brand--hardware"
                    :size="32"
                  />
                </div>
                <span class="proxy-req-card__title">{{ card.title }}</span>
                <span class="proxy-req-card__meta">{{ card.sub }}</span>
              </div>
            </div>
          </template>

          <div
            v-else-if="installOnly"
            class="os-icon-grid"
            role="radiogroup"
            :aria-label="t('nodeLifecycle.osStep')"
          >
            <button
              v-for="opt in osPickerOptions"
              :key="opt.value"
              type="button"
              class="os-icon-card"
              :class="{ 'is-checked': os === opt.value }"
              role="radio"
              :aria-checked="os === opt.value"
              :disabled="osDisabled[opt.value as 'windows' | 'macos']"
              @click="selectOs(opt.value)"
              @keydown="onOsCardKeydown($event, opt.value)"
            >
              <span class="os-icon-card__top">
                <span
                  class="os-icon-card__icon-wrap"
                  :class="`os-icon-card__icon-wrap--${opt.value}`"
                >
                  <AgentPlatformBrandIcon
                    :os="opt.value"
                    class="os-icon-card__brand"
                  />
                </span>
                <span
                  class="os-icon-card__check"
                  aria-hidden="true"
                >
                  <span />
                </span>
              </span>
              <span class="os-icon-card__name">{{ opt.label }}</span>
              <span class="os-icon-card__meta">{{ opt.meta }}</span>
            </button>
          </div>

          <ElRadioGroup
            v-else
            :model-value="os"
            class="source-radio-row"
            @update:model-value="selectOs"
          >
            <ElRadio
              value="linux"
              border
              class="source-radio-card !mr-0"
            >
              {{ t('nodesDeploy.osLinux') }}
            </ElRadio>
            <ElRadio
              value="windows"
              border
              class="source-radio-card !mr-0"
              :disabled="osDisabled.windows"
            >
              {{ t('nodesDeploy.osWindows') }}
            </ElRadio>
            <ElRadio
              value="macos"
              border
              class="source-radio-card !mr-0"
              :disabled="osDisabled.macos"
            >
              {{ t('nodesDeploy.osMacos') }}
            </ElRadio>
          </ElRadioGroup>

          <div
            v-if="installOnly && !isUbuntuHostDeploy"
            class="agent-os-support"
            :class="{ 'is-open': supportOpen }"
          >
            <button
              type="button"
              class="agent-os-support__toggle"
              :aria-expanded="supportOpen"
              @click="supportOpen = !supportOpen"
            >
              <span class="agent-os-support__toggle-main">
                <span class="agent-os-support__toggle-icon">
                  <Info
                    :size="14"
                    aria-hidden="true"
                  />
                </span>
                <span>{{ viewSupportedLabel }}</span>
              </span>
              <ChevronDown
                class="agent-os-support__chevron"
                :size="16"
                aria-hidden="true"
              />
            </button>
            <div
              v-show="supportOpen"
              class="agent-os-support__body"
            >
              <template v-if="os === 'linux'">
                <p class="agent-os-support__group">
                  {{ t('nodeLifecycle.supportedGroupDeb') }}
                </p>
                <div class="agent-os-support__grid">
                  <div
                    v-for="name in LINUX_DISTROS.deb"
                    :key="name"
                    class="agent-os-support__chip"
                  >
                    {{ name }}
                  </div>
                </div>
                <p class="agent-os-support__group">
                  {{ t('nodeLifecycle.supportedGroupRpm') }}
                </p>
                <div class="agent-os-support__grid">
                  <div
                    v-for="name in LINUX_DISTROS.rpm"
                    :key="name"
                    class="agent-os-support__chip"
                  >
                    {{ name }}
                  </div>
                </div>
                <p class="agent-os-support__group">
                  {{ t('nodeLifecycle.supportedGroupCloud') }}
                </p>
                <div class="agent-os-support__grid">
                  <div
                    v-for="name in LINUX_DISTROS.cloud"
                    :key="name"
                    class="agent-os-support__chip"
                  >
                    {{ name }}
                  </div>
                </div>
                <div class="agent-os-support__notes">
                  <p class="agent-os-support__note">
                    <strong>{{ t('nodeLifecycle.supportedLinuxAgentLabel') }}</strong>
                    <span>{{ t('nodeLifecycle.supportedLinuxAgentArch') }}</span>
                  </p>
                  <p class="agent-os-support__note">
                    <strong>{{ t('nodeLifecycle.supportedProxyGatewayLabel') }}</strong>
                    <span>{{ t('nodeLifecycle.supportedProxyGatewayUbuntu') }}</span>
                  </p>
                </div>
              </template>
              <template v-else-if="os === 'windows'">
                <p class="agent-os-support__group">
                  {{ t('nodeLifecycle.supportedGroupDesktopServer') }}
                </p>
                <div class="agent-os-support__grid">
                  <div class="agent-os-support__chip">
                    <span class="agent-os-support__chip-name">Windows 10 / 11</span>
                    <span class="agent-os-support__chip-ver">{{ t('nodeLifecycle.supportedVer64Bit') }}</span>
                  </div>
                  <div class="agent-os-support__chip">
                    <span class="agent-os-support__chip-name">Windows Server</span>
                    <span class="agent-os-support__chip-ver">{{ t('nodeLifecycle.supportedVerServer64') }}</span>
                  </div>
                </div>
              </template>
              <template v-else>
                <p class="agent-os-support__group">
                  {{ t('nodeLifecycle.supportedGroupHardware') }}
                </p>
                <div class="agent-os-support__grid">
                  <div class="agent-os-support__chip">
                    <span class="agent-os-support__chip-name">Apple Silicon</span>
                    <span class="agent-os-support__chip-ver">{{ t('nodeLifecycle.supportedVerAppleSilicon') }}</span>
                  </div>
                  <div class="agent-os-support__chip">
                    <span class="agent-os-support__chip-name">Intel Mac</span>
                    <span class="agent-os-support__chip-ver">{{ t('nodeLifecycle.supportedVerIntel64') }}</span>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <p
            v-if="linuxOnlyRoleHint && !isUbuntuHostDeploy"
            class="fullscreen-form-field__hint"
          >
            {{ linuxOnlyRoleHint }}
          </p>
        </section>
      </div>

      <div
        v-if="!maintenanceOnly && role === 'agent' && activeTab === 'install'"
        class="fullscreen-form-card"
      >
        <section class="fullscreen-form-section">
          <h3 class="fullscreen-form-section__title">
            <span class="fullscreen-form-section__indicator" />
            {{ t('nodeLifecycle.installationModeStep') }}
          </h3>
          <div class="installation-mode-picker">
            <ElRadioGroup
              v-model="selectedInstallationMode"
              class="installation-mode-grid"
              :class="{ 'installation-mode-grid--four': installationModeOptions.length === 4 }"
              :aria-label="t('nodeLifecycle.installationModeStep')"
              @change="installationModeTouched = true"
            >
              <ElRadio
                v-for="option in installationModeOptions"
                :key="option.value"
                :value="option.value"
                border
                class="installation-mode-card !mr-0"
              >
                <span class="installation-mode-card__content">
                  <span class="installation-mode-card__title-row">
                    <strong class="installation-mode-card__title">{{ option.title }}</strong>
                    <span
                      v-if="option.recommended"
                      class="installation-mode-card__badge"
                    >
                      {{ t('nodeLifecycle.installationModeRecommended') }}
                    </span>
                  </span>
                  <span class="installation-mode-card__description">{{ option.description }}</span>
                  <span class="installation-mode-card__recommendation">{{ option.recommendation }}</span>
                  <span class="installation-mode-card__details">
                    <span><b>{{ t('nodeLifecycle.installationModeScopeLabel') }}</b>{{ option.scope }}</span>
                    <span><b>{{ t('nodeLifecycle.installationModeRuntimeLabel') }}</b>{{ option.runtime }}</span>
                    <span><b>{{ t('nodeLifecycle.installationModePermissionLabel') }}</b>{{ option.permission }}</span>
                  </span>
                </span>
              </ElRadio>
            </ElRadioGroup>
            <p
              class="installation-mode-picker__hint"
              aria-live="polite"
            >
              {{ selectedInstallationMode === 'user_continuous'
                ? t('nodeLifecycle.installationModeUserContinuousHint')
                : selectedInstallationMode === 'user'
                  ? t('nodeLifecycle.installationModeUserHint')
                  : selectedInstallationMode === 'account'
                    ? t('nodeLifecycle.installationModeAccountHint')
                    : t('nodeLifecycle.installationModeSystemHint') }}
            </p>
          </div>
        </section>
      </div>

      <div class="fullscreen-form-card">
        <section class="fullscreen-form-section">
          <template v-if="installOnly">
            <h3 class="fullscreen-form-section__title">
              <span class="fullscreen-form-section__indicator" />
              {{ t('nodeLifecycle.installCommandStep') }}
            </h3>
            <I18nT
              :keypath="installLeadKey"
              scope="global"
              tag="p"
              class="fullscreen-form-field__hint agent-install-wizard__command-lead"
            >
              <template #root>
                <strong>root</strong>
              </template>
              <template #sudo>
                <strong>sudo</strong>
              </template>
              <template #cmd>
                <strong>CMD</strong>
              </template>
              <template #powershell>
                <strong>PowerShell</strong>
              </template>
              <template #administrator>
                <strong>{{ t('nodeLifecycle.installLeadAdministrator') }}</strong>
              </template>
              <template #terminal>
                <strong>{{ t('nodeLifecycle.installLeadTerminal') }}</strong>
              </template>
            </I18nT>

            <div class="source-script-shell agent-install-wizard__console">
              <div class="agent-install-wizard__console-bar">
                <span>{{ consoleBarTitle }}</span>
                <span
                  v-if="installGenerated"
                  class="agent-install-wizard__token-status"
                >
                  <span>{{ tokenValidityLabel }}</span>
                </span>
              </div>
              <div
                v-loading="loading"
                class="agent-install-wizard__console-body"
                element-loading-background="rgba(43, 45, 54, 0.88)"
              >
                <pre class="agent-install-wizard__console-pre">{{ displayCommand }}</pre>
              </div>
              <div class="agent-install-wizard__console-foot agent-install-wizard__console-foot--copy-only">
                <span
                  v-if="installGenerated"
                  class="agent-install-wizard__console-hint"
                >
                  {{ t('nodeLifecycle.installCommandReusable') }}
                </span>
                <button
                  v-if="generateOnDemand && !installGenerated"
                  type="button"
                  class="btn btn-primary agent-install-wizard__copy-btn"
                  :disabled="loading"
                  @click="generateInstallCommand"
                >
                  <RefreshCw
                    :size="12"
                    :class="{ 'is-spinning': loading }"
                    aria-hidden="true"
                  />
                  <span>{{ t('nodeLifecycle.generateInstallCommand') }}</span>
                </button>
                <button
                  v-else-if="installGenerated"
                  type="button"
                  class="btn btn-primary agent-install-wizard__copy-btn"
                  :class="{ 'agent-install-wizard__copy-btn--done': copied }"
                  :disabled="!displayCommand || loading || !tokenIsUsable"
                  @click="onCopy"
                >
                  <Check
                    v-if="copied"
                    :size="12"
                    aria-hidden="true"
                  />
                  <Copy
                    v-else
                    :size="12"
                    aria-hidden="true"
                  />
                  <span>{{ copied ? t('nodesDeploy.copied') : t('nodesDeploy.clickCopyCmd') }}</span>
                </button>
              </div>
            </div>

            <div
              v-if="localCommandWarning"
              class="add-s3-warning agent-install-wizard__warn"
              role="note"
            >
              <TriangleAlert
                :size="16"
                aria-hidden="true"
              />
              <div class="agent-install-wizard__warn-body">
                <p class="agent-install-wizard__warn-title">
                  {{ t('nodeLifecycle.localInstallCommandTitle') }}
                </p>
                <p class="agent-install-wizard__warn-desc">
                  {{ t('nodeLifecycle.localInstallCommandWarning') }}
                </p>
              </div>
            </div>

            <div
              class="install-flow-note"
              :aria-label="t('nodeLifecycle.installFlowLabel')"
            >
              <p class="install-flow-note__label">
                {{ t('nodeLifecycle.installFlowLabel') }}
              </p>
              <div class="install-flow-note__steps">
                <p class="install-flow-note__step">
                  <strong>{{ t('nodeLifecycle.installFlowStepDownload') }}</strong>
                  {{ t('nodeLifecycle.installFlowDownload') }}
                </p>
                <p class="install-flow-note__step">
                  <strong>{{ t('nodeLifecycle.installFlowStepInstall') }}</strong>
                  {{ t('nodeLifecycle.installFlowInstall') }}
                </p>
                <p class="install-flow-note__step">
                  <strong>{{ t('nodeLifecycle.installFlowStepRegister') }}</strong>
                  {{ installFlowRegisterText }}
                </p>
              </div>
            </div>
          </template>

          <template v-else>
            <div class="node-lifecycle-wizard__tabs">
              <button
                v-for="tab in visibleTabs"
                :key="tab"
                type="button"
                class="node-lifecycle-wizard__tab"
                :class="{ 'node-lifecycle-wizard__tab--active': activeTab === tab }"
                @click="activeTab = tab"
              >
                {{ t(`nodeLifecycle.tab.${tab}`) }}
              </button>
            </div>

            <div class="agent-install-wizard__body-grid">
              <div class="agent-install-wizard__platform">
                <AgentPlatformBrandIcon
                  :os="os"
                  class="agent-install-wizard__platform-icon"
                />
                <p class="agent-install-wizard__platform-name">
                  {{ roleLabel }}
                </p>
                <div
                  v-if="activeTab !== 'install'"
                  class="agent-install-wizard__platform-hints"
                >
                  <p class="agent-install-wizard__platform-hint-line">
                    <span>{{ t('nodeLifecycle.installPathLabel') }}</span>
                    <strong>{{ paths.installDir }}</strong>
                  </p>
                  <p class="agent-install-wizard__platform-hint-line">
                    <span>{{ t('nodeLifecycle.dataPathLabel') }}</span>
                    <strong>{{ paths.dataDir }}</strong>
                  </p>
                  <p class="agent-install-wizard__platform-hint-line">
                    <span>{{ t('nodeLifecycle.serviceNameLabel') }}</span>
                    <strong>{{ paths.service }}</strong>
                  </p>
                </div>
              </div>

              <div class="agent-install-wizard__command-col">
                <p class="fullscreen-form-field__hint agent-install-wizard__command-lead">
                  {{ tabHint }}
                </p>

                <ElAlert
                  v-if="activeTab === 'upgrade' && upgradeError"
                  type="error"
                  :closable="false"
                  show-icon
                  :title="upgradeError"
                />

                <div
                  v-if="activeTab === 'uninstall'"
                  class="node-lifecycle-wizard__options"
                >
                  <ElCheckbox v-model="purgeAll">
                    {{ t('nodeLifecycle.purgeAll') }}
                  </ElCheckbox>
                </div>
                <div
                  v-if="activeTab === 'service'"
                  class="node-lifecycle-wizard__options"
                >
                  <ElRadioGroup
                    v-model="serviceAction"
                    size="small"
                  >
                    <ElRadio value="status">
                      {{ t('nodeLifecycle.serviceStatus') }}
                    </ElRadio>
                    <ElRadio value="start">
                      {{ t('nodeLifecycle.serviceStart') }}
                    </ElRadio>
                    <ElRadio value="stop">
                      {{ t('nodeLifecycle.serviceStop') }}
                    </ElRadio>
                    <ElRadio value="restart">
                      {{ t('nodeLifecycle.serviceRestart') }}
                    </ElRadio>
                  </ElRadioGroup>
                </div>

                <div class="source-script-shell agent-install-wizard__console">
                  <div class="agent-install-wizard__console-bar">
                    <span>{{ consoleBarTitle }}</span>
                    <span v-if="activeTab === 'upgrade' && releaseVersion">v{{ releaseVersion }}</span>
                    <span
                      v-else-if="activeTab === 'install' && installGenerated"
                      class="agent-install-wizard__token-status"
                    >
                      <span>{{ tokenValidityLabel }}</span>
                    </span>
                  </div>
                  <div
                    v-loading="loading && (activeTab === 'install' || activeTab === 'upgrade')"
                    class="agent-install-wizard__console-body"
                    element-loading-background="rgba(43, 45, 54, 0.88)"
                  >
                    <div
                      v-if="loading && activeTab === 'upgrade'"
                      class="proxy-install-wizard__generating"
                    >
                      <RefreshCw
                        class="proxy-install-wizard__generating-icon"
                        :size="14"
                        aria-hidden="true"
                      />
                      <span>{{ t('nodeLifecycle.upgradeLoading') }}</span>
                    </div>
                    <pre
                      v-else
                      class="agent-install-wizard__console-pre"
                    >{{ displayCommand }}</pre>
                  </div>
                  <div class="agent-install-wizard__console-foot">
                    <span class="agent-install-wizard__console-hint">
                      <template v-if="activeTab === 'install' && installGenerated">
                        {{ t('nodeLifecycle.installCommandReusable') }}
                      </template>
                      <template v-else>
                        {{ footnote }}
                      </template>
                    </span>
                    <button
                      type="button"
                      class="btn btn-primary agent-install-wizard__copy-btn"
                      :class="{ 'agent-install-wizard__copy-btn--done': copied }"
                      :disabled="!displayCommand || loading || (activeTab === 'install' && !tokenIsUsable)"
                      @click="onCopy"
                    >
                      <Check
                        v-if="copied"
                        :size="12"
                        aria-hidden="true"
                      />
                      <Copy
                        v-else
                        :size="12"
                        aria-hidden="true"
                      />
                      <span>{{ copied ? t('nodesDeploy.copied') : t('nodesDeploy.clickCopyCmd') }}</span>
                    </button>
                  </div>
                </div>

                <div
                  v-if="localCommandWarning"
                  class="add-s3-warning agent-install-wizard__warn"
                  role="note"
                >
                  <TriangleAlert
                    class="add-s3-warning__icon"
                    :size="16"
                    stroke-width="2"
                  />
                  <div class="agent-install-wizard__warn-body">
                    <p class="agent-install-wizard__warn-desc">
                      {{ localCommandWarningText }}
                    </p>
                  </div>
                </div>

                <div
                  v-if="roleNote"
                  class="add-s3-warning agent-install-wizard__warn"
                  role="note"
                >
                  <TriangleAlert
                    class="add-s3-warning__icon"
                    :size="16"
                    stroke-width="2"
                  />
                  <div class="agent-install-wizard__warn-body">
                    <p class="agent-install-wizard__warn-title">
                      {{ t('nodesDeploy.notesTitle') }}
                    </p>
                    <p class="agent-install-wizard__warn-desc">
                      {{ roleNote }}
                    </p>
                  </div>
                </div>

                <div
                  v-if="activeTab === 'upgrade'"
                  class="add-s3-warning agent-install-wizard__warn"
                  role="note"
                >
                  <TriangleAlert
                    class="add-s3-warning__icon"
                    :size="16"
                    stroke-width="2"
                  />
                  <div class="agent-install-wizard__warn-body">
                    <p class="agent-install-wizard__warn-desc">
                      {{ t('nodeLifecycle.upgradeOnlineHint') }}
                    </p>
                    <p class="agent-install-wizard__warn-desc">
                      {{ t('nodeLifecycle.upgradeInterruptHint') }}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-lifecycle-wizard__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.node-lifecycle-wizard__tab {
  border: 1px solid rgb(226 232 240);
  background: rgb(248 250 252);
  color: rgb(51 65 85);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}

.node-lifecycle-wizard__tab--active {
  background: var(--color-info-light);
  border-color: var(--color-info-border);
  color: var(--color-info);
  font-weight: 600;
}

.node-lifecycle-wizard__options {
  margin-bottom: 12px;
}

.installation-mode-picker {
  display: grid;
  gap: 8px;
}

.installation-mode-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  align-items: stretch;
}

.installation-mode-grid--four {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.installation-mode-card {
  display: flex;
  align-items: flex-start;
  min-width: 0;
  min-height: 224px;
  height: 100%;
  margin: 0;
  padding: 14px 16px;
  white-space: normal;
}

.installation-mode-card :deep(.el-radio__label) {
  display: block;
  min-width: 0;
  width: 100%;
  height: 100%;
  padding-left: 8px;
  white-space: normal;
}

.installation-mode-card__content {
  display: grid;
  gap: 6px;
  height: 100%;
  min-width: 0;
  grid-template-rows: auto auto auto 1fr;
}

.installation-mode-card__title {
  color: var(--color-text-primary);
  font-size: 14px;
  line-height: 1.35;
}

.installation-mode-card__title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.installation-mode-card__badge {
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--color-primary-light, #f2f0fe);
  color: var(--color-primary, #6d5ef6);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
}

.installation-mode-card__description,
.installation-mode-card__recommendation,
.installation-mode-card__details {
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

.installation-mode-card__recommendation {
  color: var(--color-info);
  font-weight: 600;
}

.installation-mode-card__details {
  display: grid;
  gap: 2px;
  padding-top: 4px;
  border-top: 1px solid var(--color-border-light);
}

.installation-mode-card__details b {
  color: var(--color-text-secondary);
  font-weight: 600;
}

@media (max-width: 920px) {
  .installation-mode-grid,
  .installation-mode-grid--four {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 680px) {
  .installation-mode-grid {
    grid-template-columns: 1fr;
  }

  .installation-mode-card {
    min-height: 0;
  }
}

.installation-mode-picker__hint {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

.agent-install-wizard--maintenance {
  container-type: inline-size;
}

.agent-install-wizard--maintenance .fullscreen-form-step-stack {
  gap: 0;
}

.agent-install-wizard--maintenance .fullscreen-form-section {
  padding: 18px 20px 20px;
}

.agent-install-wizard--maintenance .agent-install-wizard__body-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform {
  display: grid;
  grid-template-columns: 44px auto minmax(0, 1fr);
  align-items: center;
  gap: 8px 14px;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid rgb(226 232 240);
  border-radius: 10px;
  background: rgb(248 250 252);
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-icon {
  display: block;
  width: 40px;
  height: 40px;
  filter: drop-shadow(0 2px 4px rgb(15 23 42 / 8%));
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-name {
  margin: 0;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hints {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 4px 12px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  margin: 0;
  color: rgb(148 163 184);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line strong {
  color: rgb(71 85 105);
  font-weight: 500;
}

.agent-install-wizard--maintenance .agent-install-wizard__command-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__command-lead {
  margin: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__console.source-script-shell {
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 1px solid #2b2d36;
  border-radius: 10px;
  background: #2b2d36;
}

.agent-install-wizard--maintenance .agent-install-wizard__console-bar,
.agent-install-wizard--maintenance .agent-install-wizard__console-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: #25262e;
}

.agent-install-wizard--maintenance .agent-install-wizard__console-bar {
  border-bottom: 1px solid #3a3b45;
  color: #c9cdd4;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
}

.agent-install-wizard--maintenance .agent-install-wizard__console-body {
  position: relative;
  min-height: 88px;
  max-height: 240px;
  padding: 12px 14px;
  overflow: auto;
}

.agent-install-wizard--maintenance .agent-install-wizard__console-pre {
  margin: 0;
  color: #d9f7be;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.agent-install-wizard--maintenance .agent-install-wizard__console-foot {
  border-top: 1px solid #3a3b45;
}

.agent-install-wizard--maintenance .agent-install-wizard__console-hint {
  flex: 1 1 auto;
  min-width: 0;
  color: #86909c;
  font-size: 12px;
  line-height: 1.45;
}

.agent-install-wizard--maintenance .agent-install-wizard__copy-btn {
  flex: 0 0 auto;
}

.agent-install-wizard--maintenance .agent-install-wizard__warn {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 0;
  padding: 11px 12px;
  border: 1px solid rgb(253 230 138);
  border-radius: 8px;
  background: rgb(255 251 235);
  color: rgb(146 64 14);
}

.agent-install-wizard--maintenance .agent-install-wizard__warn > svg {
  flex: 0 0 auto;
  margin-top: 2px;
}

.agent-install-wizard--maintenance .agent-install-wizard__warn-body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__warn-title,
.agent-install-wizard--maintenance .agent-install-wizard__warn-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
}

.agent-install-wizard--maintenance .agent-install-wizard__warn-title {
  font-weight: 600;
}

@container (max-width: 680px) {
  .agent-install-wizard--maintenance .agent-install-wizard__platform {
    grid-template-columns: 40px minmax(0, 1fr);
  }

  .agent-install-wizard--maintenance .agent-install-wizard__platform-hints {
    grid-column: 1 / -1;
    grid-template-columns: 1fr;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__console-foot {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
