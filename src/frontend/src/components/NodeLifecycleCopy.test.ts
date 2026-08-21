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
    expect(locale).toContain("installationModeStep: 'Choose Agent Run Mode'")
    expect(locale).toContain("installationModeSystem: 'System Service'")
    expect(locale).toContain("installationModeUser: 'Current User'")
    expect(locale).toContain("generateInstallCommand: 'Generate install command'")
    expect(locale).toContain('Copy the command and run it in a shell on the target host')
    expect(locale).toContain("installFlowDownload: 'Downloads the small installer and checks the target host'")
    expect(locale).toContain("installFlowInstall: 'Downloads the required components and installs the Agent'")
  })

  it('presents operating system, run mode, and command as separate install steps', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const locale = source('src/locales/en.ts')

    expect(wizard).toMatch(
      /fullscreen-form-card[\s\S]*?nodeLifecycle\.osStep[\s\S]*?<\/div>\s*\n\s*<div[\s\S]*?fullscreen-form-card[\s\S]*?nodeLifecycle\.installationModeStep[\s\S]*?<\/div>\s*\n\s*<div class="fullscreen-form-card">[\s\S]*?nodeLifecycle\.installCommandStep/,
    )
    expect(locale).toContain('Runs continuously after sign-out')
    expect(locale).toContain('pauses after sign-out')
    expect(locale).toContain('provides continuous monitoring')
    expect(locale).toContain('provides monitoring while that user is signed in')
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
