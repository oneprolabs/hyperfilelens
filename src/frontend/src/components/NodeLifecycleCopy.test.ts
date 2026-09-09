import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

describe('Node lifecycle copy', () => {
  it('keeps headings and action copy consistent', () => {
    const locale = source('src/locales/en.ts')
    const chinese = JSON.parse(source('../../language-packs/packs/zh-hans/frontend/messages.json'))
    const spanish = JSON.parse(source('../../language-packs/packs/es/frontend/messages.json'))
    const css = source('src/styles/agent-install-wizard.css')

    expect(locale).toContain("installCommandStep: 'Run the Install Command'")
    expect(locale).toContain("installationModeSystem: 'Host files · continuous'")
    expect(locale).toContain("installationModeUser: 'Current user files'")
    expect(locale).toContain("installationModeUserContinuous: 'User files · continuous'")
    expect(locale).toContain("installationModeAccount: 'Specified-user files · continuous'")
    expect(locale).toContain('installationModeSystemPermission:')
    expect(locale).toContain('installationModeUserPermission:')
    expect(locale).toContain('installationModeUserContinuousPermission:')
    expect(wizardSource()).toContain("const isNewAgentInstallation = computed(() => props.role === 'agent' && props.nodeId == null)")
    expect(wizardSource()).toContain("return 'nodeLifecycle.installLeadAutomaticLinux'")
    expect(wizardSource()).toContain("return 'nodeLifecycle.installLeadAutomaticWindows'")
    expect(wizardSource()).toContain("return 'nodeLifecycle.installLeadAutomaticMacos'")
    expect(wizardSource()).toContain("...(isNewAgentInstallation.value\n              ? {}")
    expect(wizardSource()).toContain("os === 'linux' ? 'user_continuous' : 'user'")
    expect(wizardSource()).not.toContain('selectedInstallationMode')
    expect(wizardSource()).not.toContain('installationModeOptions')
    expect(locale).toContain('Run the command below in a shell on the target Linux host.')
    expect(locale).toContain('Run the command below in PowerShell on the target Windows host.')
    expect(locale).toContain('Run the command below in Terminal on the target Mac.')
    expect(locale).toContain('The installer shows the installation mode before proceeding.')
    expect(locale).not.toContain('The installer confirms the installation mode before proceeding.')
    expect(locale).toContain('grant HyperFileLens Agent Full Disk Access')
    expect(chinese.nodeLifecycle.installLeadAutomaticLinux).toContain('Linux')
    expect(chinese.nodeLifecycle.installLeadAutomaticWindows).toContain('PowerShell')
    expect(chinese.nodeLifecycle.installLeadAutomaticMacos).toContain('Mac')
    for (const message of [
      chinese.nodeLifecycle.installLeadAutomaticLinux,
      chinese.nodeLifecycle.installLeadAutomaticWindows,
      chinese.nodeLifecycle.installLeadAutomaticMacos,
    ]) {
      expect(message).toContain('\n')
    }
    expect(spanish.nodeLifecycle.installLeadAutomaticLinux).toContain('Ejecute el siguiente comando en una terminal del host Linux de destino.\nAcceso:')
    expect(spanish.nodeLifecycle.installLeadAutomaticWindows).toContain('Ejecute el siguiente comando en PowerShell en el equipo Windows de destino.\nAcceso:')
    expect(spanish.nodeLifecycle.installLeadAutomaticMacos).toContain('Ejecute el siguiente comando en Terminal en el Mac de destino.\nAcceso:')
    expect(css).toMatch(/agent-install-wizard__command-lead[\s\S]*?white-space: pre-line/)
    expect(locale).toContain("generateInstallCommand: 'Generate install command'")
    expect(locale).toContain('Copy the command and run it in a shell on the target host')
    expect(locale).toContain("installFlowDownload: 'Downloads the small installer and checks the target host'")
    expect(locale).toContain("installFlowInstallAgent: 'Downloads the required components and installs the Agent'")
    expect(locale).toContain("installFlowInstallProxy: 'Downloads the required components and installs the Proxy'")
    expect(locale).toContain("installFlowInstallGateway: 'Downloads and installs the Data Gateway components'")
  })

  it('presents operating system and command without a protection-mode picker', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const locale = source('src/locales/en.ts')

    expect(wizard).toMatch(
      /fullscreen-form-card[\s\S]*?nodeLifecycle\.osStep[\s\S]*?<\/div>\s*\n\s*<div class="fullscreen-form-card">[\s\S]*?nodeLifecycle\.installCommandStep/,
    )
    expect(wizard).not.toContain("t('nodeLifecycle.installationModeStep')")
    expect(locale).toContain('Continuous, including after sign-out and restart')
    expect(locale).toContain('While this user is signed in')
    expect(locale).toContain('Selected files and folders on this host')
    expect(locale).toContain('Files this user can read')
    expect(locale).toContain('Files the selected account can read')
    expect(locale).toContain('Administrator authorization required')
    expect(locale).toContain('Administrator access required')
    expect(locale).toContain("installationModeSystemHint: 'Requires administrator authorization and runs as a host service.'")
    expect(locale).toContain("installationModeUserHint: 'Runs with the signed-in user\\'s access.'")
    expect(locale).toContain("installationModeUserContinuousHint: 'Continues after SSH disconnect.'")
    expect(locale).toContain('Current-user permission only')
    expect(locale).toContain('grant HyperFileLens Agent Full Disk Access in System Settings')
  })

  it('uses accurate role-specific platform and storage guidance', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const locale = source('src/locales/en.ts')

    expect(wizard).toContain("props.role === 'gateway'")
    expect(wizard).toContain("t('nodesDeploy.gatewayReqDiskSub')")
    expect(locale).toContain("proxyReqDisk: '50GB+ storage'")
    expect(locale).not.toContain('100GB+')
    expect(locale).toContain('Ubuntu 20.04, 22.04, or 24.04 LTS')
    expect(locale).toContain('amd64')
    expect(locale).toContain("gatewayReqDiskSub: 'Local runtime and workspace storage'")
    expect(locale).toContain('Registers the Public Data Gateway with HyperFileLens')
    expect(locale).toContain('Registers the Private Data Gateway with HyperFileLens')
  })

  it('uses product-role terminology in install and maintenance summaries', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const locale = source('src/locales/en.ts')

    expect(wizard).toContain("agent: 'nodeLifecycle.installFlowInstallAgent'")
    expect(wizard).toContain("proxy: 'nodeLifecycle.installFlowInstallProxy'")
    expect(wizard).toContain("gateway: 'nodeLifecycle.installFlowInstallGateway'")
    expect(wizard).toContain("agent: 'nodeLifecycle.installedAgentTitle'")
    expect(wizard).toContain("proxy: 'nodeLifecycle.installedProxyTitle'")
    expect(wizard).toContain("gateway: 'nodeLifecycle.installedGatewayTitle'")
    expect(wizard).toContain('{{ installFlowInstallText }}')
    expect(wizard).toContain('<h3>{{ installedComponentTitle }}</h3>')
    expect(locale).toContain("installedAgentTitle: 'Installed Agent'")
    expect(locale).toContain("installedProxyTitle: 'Installed Proxy'")
    expect(locale).toContain("installedGatewayTitle: 'Installed Data Gateway'")
  })

  it('revokes only enrollment tokens discarded before their command is displayed', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')

    expect(wizard).toContain('await revokeIssuedEnrollment(issued.tokenId, platformEnrollment)')
    expect(wizard).not.toContain('void revokeIssuedEnrollment(staleTokenId, staleTokenIsPlatform)')
    expect(wizard).toContain('await revokeEnrollmentToken(tokenId).catch(() => undefined)')
    expect(wizard).toContain('fetchNodeMaintenanceRelease')
    expect(wizard).not.toContain('createNodeToken({ role: props.role')
    expect(wizard).not.toContain('installError.value')
  })

  it('refreshes maintenance commands when the persisted installation mode changes', () => {
    const wizard = wizardSource()

    expect(wizard).toMatch(
      /props\.gatewayScope,\s*props\.installationMode,\s*\] as const/,
    )
    expect(wizard).toContain('refreshAll()')
  })

  it('shows expiry without host quotas or replacement-command controls', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const locale = source('src/locales/en.ts')

    expect(wizard).toContain('tokenValidityLabel')
    expect(locale).toContain("installCommandValidFor: 'Valid for {hours}h {minutes}m'")
    expect(wizard).not.toContain('tokenCapacityLabel')
    expect(wizard).not.toContain('replaceInstallCommand')
    expect(locale).not.toContain('installs left')
    expect(locale).not.toContain("generateNewInstallCommand: 'New command'")
  })

  it('styles install-flow steps outside resource-add fullscreen layouts', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const css = source('src/styles/agent-install-wizard.css')

    expect(wizard).toContain("t('nodeLifecycle.installFlowStepDownload')")
    expect(wizard).toMatch(
      /installFlowStepDownload[\s\S]*?<\/strong>\s*\n\s*\{\{ t\('nodeLifecycle\.installFlowDownload'\) \}\}/,
    )
    expect(css).toContain('.agent-install-wizard--source-host .install-flow-note__step strong')
    expect(css).toContain('display: block')
    expect(css).not.toContain(
      '.resource-add-fullscreen .agent-install-wizard--source-host .install-flow-note__step strong',
    )
  })
})

function wizardSource() {
  return source('src/components/NodeLifecycleWizard.vue')
}
