<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { I18nT, useI18n } from 'vue-i18n'
import {
  Copy,
  Check,
  TriangleAlert,
  RefreshCw,
  ChevronDown,
  Info,
  Cpu,
  MemoryStick,
  HardDrive,
} from 'lucide-vue-next'
import AgentPlatformBrandIcon from './agent-deploy/AgentPlatformBrandIcon.vue'
import UbuntuBrandIcon from './agent-deploy/UbuntuBrandIcon.vue'
import {
  buildLocalServiceCommand,
  buildLocalUninstallCommand,
  buildLocalUpgradeCommand,
  defaultPackagePath,
  installPathsSummary,
  isLinuxOnlyRole,
  maintenanceExecutionKind,
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
const keepData = ref(false)
const serviceAction = ref<'status' | 'start' | 'stop' | 'restart'>(props.initialServiceAction)
const defaultInstallationModeForOs = (os: EnrollmentOs): NodeInstallationMode => (
  os === 'linux' ? 'user_continuous' : 'user'
)
const effectiveInstallationMode = computed<NodeInstallationMode>(() => (
  props.role === 'agent'
    ? props.installationMode ?? defaultInstallationModeForOs(props.os)
    : 'system'
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

const maintenanceModeLabel = computed(() => {
  const keys: Record<NodeInstallationMode, string> = {
    system: 'nodeLifecycle.installationModeSystem',
    user: 'nodeLifecycle.installationModeUser',
    user_continuous: 'nodeLifecycle.installationModeUserContinuous',
    account: 'nodeLifecycle.installationModeAccount',
  }
  return t(keys[effectiveInstallationMode.value])
})

const maintenanceOsLabel = computed(() => (
  osPickerOptions.value.find((option) => option.value === props.os)?.label ?? props.os
))

const maintenanceExecution = computed(() => {
  const kind = maintenanceExecutionKind(props.os, effectiveInstallationMode.value)
  return {
    title: t(`nodeLifecycle.execution.${kind}.title`),
    description: t(`nodeLifecycle.execution.${kind}.description`),
  }
})

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

const isNewAgentInstallation = computed(() => props.role === 'agent' && props.nodeId == null)

const installLeadKey = computed(() => {
  if (isNewAgentInstallation.value) {
    return props.os === 'macos'
      ? 'nodeLifecycle.installLeadAutomaticMacos'
      : 'nodeLifecycle.installLeadAutomatic'
  }
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
  if (props.maintenanceOnly) return maintenanceExecution.value.title
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
            ...(isNewAgentInstallation.value
              ? {}
              : { installationMode: effectiveInstallationMode.value }),
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
    keepData.value,
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
  () => [
    props.orgKey,
    props.nodeId,
    props.role,
    props.os,
    props.gatewayScope,
    props.installationMode,
  ] as const,
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

watch([keepData, serviceAction, () => props.os], () => refreshStaticCommands())

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
            <div class="agent-install-wizard__body-grid">
              <div class="agent-install-wizard__platform">
                <div class="agent-install-wizard__section-title">
                  <span class="fullscreen-form-section__indicator" />
                  <h3>{{ t('nodeLifecycle.installedAgentTitle') }}</h3>
                </div>
                <div class="agent-install-wizard__platform-summary">
                  <span class="agent-install-wizard__platform-icon-wrap">
                    <AgentPlatformBrandIcon
                      :os="os"
                      class="agent-install-wizard__platform-icon"
                    />
                  </span>
                  <span class="agent-install-wizard__platform-copy">
                    <span class="agent-install-wizard__platform-eyebrow">
                      {{ maintenanceOsLabel }} · {{ roleLabel }}
                    </span>
                    <strong class="agent-install-wizard__platform-name">
                      {{ maintenanceModeLabel }}
                    </strong>
                  </span>
                </div>

                <div class="agent-install-wizard__execution">
                  <span class="agent-install-wizard__execution-label">
                    {{ t('nodeLifecycle.executionLabel') }}
                  </span>
                  <strong>{{ maintenanceExecution.title }}</strong>
                  <span>{{ maintenanceExecution.description }}</span>
                </div>

                <dl
                  v-if="activeTab !== 'install'"
                  class="agent-install-wizard__platform-hints"
                >
                  <div class="agent-install-wizard__platform-hint-line">
                    <dt>{{ t('nodeLifecycle.installPathLabel') }}</dt>
                    <dd>{{ paths.installDir }}</dd>
                  </div>
                  <div class="agent-install-wizard__platform-hint-line">
                    <dt>{{ t('nodeLifecycle.dataPathLabel') }}</dt>
                    <dd>{{ paths.dataDir }}</dd>
                  </div>
                  <div class="agent-install-wizard__platform-hint-line">
                    <dt>{{ t('nodeLifecycle.serviceNameLabel') }}</dt>
                    <dd>{{ paths.service }}</dd>
                  </div>
                </dl>
              </div>

              <div class="agent-install-wizard__command-col">
                <div class="agent-install-wizard__command-head">
                  <div>
                    <div class="agent-install-wizard__section-title">
                      <span class="fullscreen-form-section__indicator" />
                      <h3>{{ t('nodeLifecycle.maintenanceCommands') }}</h3>
                    </div>
                    <p class="fullscreen-form-field__hint agent-install-wizard__command-lead">
                      {{ tabHint }}
                    </p>
                  </div>
                  <div
                    class="node-lifecycle-wizard__tabs"
                    role="tablist"
                    :aria-label="t('nodeLifecycle.maintenanceCommands')"
                  >
                    <button
                      v-for="tab in visibleTabs"
                      :key="tab"
                      type="button"
                      class="node-lifecycle-wizard__tab"
                      :class="{ 'node-lifecycle-wizard__tab--active': activeTab === tab }"
                      role="tab"
                      :aria-selected="activeTab === tab"
                      @click="activeTab = tab"
                    >
                      {{ t(`nodeLifecycle.tab.${tab}`) }}
                    </button>
                  </div>
                </div>

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
                  <ElCheckbox v-model="keepData">
                    {{ t('nodeLifecycle.keepData') }}
                  </ElCheckbox>
                </div>
                <div
                  v-if="activeTab === 'service'"
                  class="node-lifecycle-wizard__options"
                >
                  <ElRadioGroup
                    v-model="serviceAction"
                    size="small"
                    :aria-label="t('nodeLifecycle.maintenanceCommands')"
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
                  class="add-s3-warning agent-install-wizard__warn agent-install-wizard__warn--warning"
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
  display: inline-flex;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.node-lifecycle-wizard__tab {
  min-width: 78px;
  min-height: 34px;
  padding: 0 13px;
  border: 0;
  color: var(--color-text-secondary);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.node-lifecycle-wizard__tab + .node-lifecycle-wizard__tab {
  border-left: 1px solid var(--el-border-color-lighter);
}

.node-lifecycle-wizard__tab--active {
  color: #fff;
  background: var(--color-primary);
  font-weight: 600;
}

.node-lifecycle-wizard__tab:hover:not(.node-lifecycle-wizard__tab--active) {
  color: var(--color-primary);
  background: var(--color-primary-light);
}

.node-lifecycle-wizard__tab:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: -3px;
}

.node-lifecycle-wizard__options {
  margin-bottom: 12px;
}

.agent-install-wizard--maintenance {
  container-type: inline-size;
}

.agent-install-wizard--maintenance .fullscreen-form-step-stack {
  gap: 0;
}

.agent-install-wizard--maintenance .fullscreen-form-section {
  padding: 0;
}

.agent-install-wizard--maintenance > .fullscreen-form-step-stack > .fullscreen-form-card {
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.agent-install-wizard--maintenance .agent-install-wizard__body-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform,
.agent-install-wizard--maintenance .agent-install-wizard__command-col {
  min-width: 0;
  padding: 18px 20px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-fill-color-blank);
  box-shadow: 0 5px 14px rgb(15 23 42 / 4%);
}

.agent-install-wizard--maintenance .agent-install-wizard__platform {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.agent-install-wizard--maintenance .agent-install-wizard__section-title {
  display: flex;
  grid-column: 1 / -1;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__section-title h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 14px;
  font-weight: 650;
  line-height: 18px;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-summary {
  display: flex;
  grid-column: 1;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__execution {
  display: grid;
  align-content: center;
  gap: 5px;
  min-width: 0;
  grid-column: 2;
  padding-left: 20px;
  border-left: 1px solid var(--el-border-color-lighter);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.agent-install-wizard--maintenance .agent-install-wizard__execution-label {
  padding: 0;
  color: var(--color-text-secondary);
  background: transparent;
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
}

.agent-install-wizard--maintenance .agent-install-wizard__execution strong {
  color: var(--color-text-primary);
  font-size: 15px;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hints {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  min-width: 0;
  margin: 0;
  padding: 14px 0 0;
  border-top: 1px solid var(--el-border-color-lighter);
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line {
  display: grid;
  gap: 4px;
  min-width: 0;
  margin: 0;
  padding: 0 14px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line:first-child {
  padding-left: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line + .agent-install-wizard__platform-hint-line {
  border-left: 1px solid var(--el-border-color-light);
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line dt,
.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line dd {
  margin: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line dd {
  color: var(--color-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
}

.agent-install-wizard--maintenance .agent-install-wizard__command-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.agent-install-wizard--maintenance .agent-install-wizard__command-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-icon-wrap {
  display: grid;
  flex: 0 0 auto;
  width: 48px;
  height: 48px;
  place-items: center;
  border: 1px solid var(--el-border-color-light);
  border-radius: 11px;
  background: rgb(248 250 252);
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-icon {
  display: block;
  width: 32px;
  height: 32px;
  filter: drop-shadow(0 2px 4px rgb(15 23 42 / 8%));
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-eyebrow {
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 500;
  line-height: 1.4;
}

.agent-install-wizard--maintenance .agent-install-wizard__platform-name {
  color: var(--color-text-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.agent-install-wizard--maintenance .agent-install-wizard__command-lead {
  margin: 0;
}

.agent-install-wizard--maintenance .node-lifecycle-wizard__options {
  margin: -2px 0 0;
}

.agent-install-wizard--maintenance .node-lifecycle-wizard__options :deep(.el-radio-group) {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 18px;
}

.agent-install-wizard--maintenance .node-lifecycle-wizard__options :deep(.el-radio) {
  margin-right: 0;
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
  padding: 3px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--color-text-secondary);
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

.agent-install-wizard--maintenance .agent-install-wizard__warn--warning {
  color: #b54708;
}

@container (max-width: 680px) {
  .agent-install-wizard--maintenance .agent-install-wizard__platform-hints {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line {
    padding: 0;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__platform-hint-line + .agent-install-wizard__platform-hint-line {
    padding-top: 10px;
    border-top: 1px solid rgb(241 245 249);
    border-left: 0;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__console-foot {
    align-items: flex-start;
    flex-direction: column;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__platform-summary {
    grid-column: 1;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__execution {
    grid-column: 1;
    padding: 14px 0 0;
    border-top: 1px solid var(--el-border-color-lighter);
    border-left: 0;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__platform {
    grid-template-columns: 1fr;
  }

  .agent-install-wizard--maintenance .agent-install-wizard__command-head {
    align-items: stretch;
    flex-direction: column;
  }

  .agent-install-wizard--maintenance .node-lifecycle-wizard__tabs {
    display: flex;
  }

  .agent-install-wizard--maintenance .node-lifecycle-wizard__tab {
    flex: 1;
  }
}
</style>
