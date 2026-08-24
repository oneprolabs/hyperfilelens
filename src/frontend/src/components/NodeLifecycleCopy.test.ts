import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

describe('Node lifecycle copy', () => {
  it('keeps headings and action copy consistent', () => {
    const locale = source('src/locales/en.ts')

    expect(locale).toContain("installCommandStep: 'Run the Install Command'")
    expect(locale).toContain("installationModeStep: 'Select Protection Mode'")
    expect(locale).toContain("installationModeSystem: 'Host files · continuous'")
    expect(locale).toContain("installationModeUser: 'Current user files'")
    expect(locale).toContain("installationModeUserContinuous: 'User files · continuous'")
    expect(locale).toContain("installationModeAccount: 'Specified-user files · continuous'")
    expect(locale).toContain('installationModeSystemPermission:')
    expect(locale).toContain('installationModeUserPermission:')
    expect(locale).toContain('installationModeUserContinuousPermission:')
    expect(wizardSource()).toContain("const isNewAgentInstallation = computed(() => props.role === 'agent' && props.nodeId == null)")
    expect(wizardSource()).toContain("props.role === 'agent' && props.nodeId == null\n    ? defaultInstallationModeForOs(props.os)")
    expect(wizardSource()).toContain("if (option.value === 'account') return false")
    expect(wizardSource()).toContain("return option.value === 'user_continuous' || option.value === 'system'")
    expect(wizardSource()).toContain("return option.value === 'user' || option.value === 'system'")
    expect(wizardSource()).toContain("os === 'linux' ? 'user_continuous' : 'user'")
    expect(wizardSource()).toContain('const defaultMode = defaultInstallationModeForOs(props.os)')
    expect(wizardSource()).toContain('selectedInstallationMode.value = defaultMode')
    expect(locale).toContain("installationModeRecommended: 'Recommended'")
    expect(locale).toContain("generateInstallCommand: 'Generate install command'")
    expect(locale).toContain('Copy the command and run it in a shell on the target host')
    expect(locale).toContain("installFlowDownload: 'Downloads the small installer and checks the target host'")
    expect(locale).toContain("installFlowInstall: 'Downloads the required components and installs the Agent'")
  })

  it('presents operating system, protection mode, and command as separate install steps', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const locale = source('src/locales/en.ts')

    expect(wizard).toMatch(
      /fullscreen-form-card[\s\S]*?nodeLifecycle\.osStep[\s\S]*?<\/div>\s*\n\s*<div[\s\S]*?fullscreen-form-card[\s\S]*?nodeLifecycle\.installationModeStep[\s\S]*?<\/div>\s*\n\s*<div class="fullscreen-form-card">[\s\S]*?nodeLifecycle\.installCommandStep/,
    )
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
    expect(locale).toContain('Registers a Public Data Gateway with HyperFileLens')
    expect(locale).toContain('Registers a Private Data Gateway with HyperFileLens')
  })

  it('revokes enrollment tokens discarded by command regeneration', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')

    expect(wizard).toContain('await revokeIssuedEnrollment(issued.tokenId, platformEnrollment)')
    expect(wizard).toContain('void revokeIssuedEnrollment(staleTokenId, staleTokenIsPlatform)')
    expect(wizard).toContain('enrollmentTokenIsPlatform.value')
    expect(wizard).toContain('await revokeEnrollmentToken(tokenId).catch(() => undefined)')
    expect(wizard).toContain('fetchNodeMaintenanceRelease')
    expect(wizard).not.toContain('createNodeToken({ role: props.role')
    expect(wizard).not.toContain('installError.value')
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
