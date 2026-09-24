import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compactSourceText } from '../test/sourceText'

function source(path: string) {
  return compactSourceText(readFileSync(resolve(process.cwd(), path), 'utf8'))
}

describe('Admin Console finalization contracts', () => {
  it('matches the tenant Private Gateway add shell for Public Data Gateway enrollment', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')
    const addPage = source('src/platform-ops/pages/engine/PlatformGatewayAdd.vue')

    expect(addPage).toContain('resource-add-fullscreen')
    expect(addPage).toContain('proxy-deploy-fullscreen')
    expect(addPage).toContain("<Teleport to=\"body\">")
    expect(addPage).toContain("t('nodesDeploy.pageTitlePublicGateway')")
    expect(addPage).toContain("t('nodesDeploy.publicGatewayIntroDesc')")
    expect(addPage).not.toContain('generate-on-demand')
    expect(addPage).not.toContain('platformOps.engineGateway.securityNote')
    expect(addPage).not.toContain('platform-gateway-add__security')
    expect(addPage).not.toContain('enrollment-ttl-seconds')
    expect(addPage).not.toContain('gateway-token-ttl')
    expect(addPage).toContain('@enrollment-issued="onEnrollmentIssued"')
    expect(addPage).toContain('gateway-scope="platform"')
    // Wizard still supports on-demand generation for other callers; Admin Public no longer uses it.
    expect(wizard).toContain("activeTab.value === 'install' && !props.generateOnDemand")
    expect(wizard).toContain('v-if="generateOnDemand && !installGenerated"')
  })

  it('keeps the Admin gateway table compact and uses Admin pagination', () => {
    const gateways = source('src/pages/insight/InsightDataGateways.vue')

    expect(gateways).toContain("'/platform-ops/engine/gateways/add'")
    expect(gateways).toContain('<PlatformOpsPagination')
    expect(gateways).toContain('v-if="!isPlatformEngine" label="OS"')
    expect(gateways).toContain('v-if="!isPlatformEngine" :label="t(\'protection.sourceResources.colCapacity\')"')
    expect(gateways).toContain("t('platformOps.engineGateway.colCapacity')")
    expect(gateways).toContain('fetchPublicGatewayCapacities')
    expect(gateways).toContain('patchPublicGatewayCapacity')
    expect(gateways).toContain('capacityHasKnownTotal')
    expect(gateways).toContain(':known-total="capacityHasKnownTotal(capacityFor(row))"')
    expect(gateways).toContain('fixed="right"')
  })

  it('hides the outer Admin title on every AI Engine editor route', () => {
    const layout = source('src/platform-ops/layout/PlatformEngineLayout.vue')
    expect(layout).toContain("/\\/(?:add|edit)$/")
    expect(layout).toContain(':hide-page-title="hidePageTitle"')
  })

  it('keeps tenant list labels while using resource-specific Admin actions', () => {
    const models = source('src/pages/insight/InsightAiSettings.vue')
    expect(models).toContain("isPlatformEngine ? t('platformOps.engineActions.addModel') : t('insight.aiSettings.btnAdd')")
    expect(models).toContain("isPlatformEngine ? t('platformOps.engineActions.modelActions') : t('insight.aiSettings.btnMoreActions')")
  })

  it('shows runtime environment summary, infrastructure health, and service connections', () => {
    const environment = source('src/platform-ops/pages/platform/settings/EnvironmentSettings.vue')

    expect(environment).toContain("t('platformOps.settings.environment.overviewTitle')")
    expect(environment).toContain("t('platformOps.settings.environment.controlPlaneTitle')")
    expect(environment).toContain("RuntimeStatusTable :rows=\"[controlPlaneOverviewRow, ...controlPlaneRows]\"")
    expect(environment).toContain('const controlPlaneHealth = computed(() =>')
    expect(environment).toContain('probeStatus(dataGatewayProbe.value),')
    expect(environment).toContain('health: healthCell(controlPlaneHealth.value)')
    expect(environment).toContain('<RuntimeServiceConnections :data-gateway="dataGatewayProbe" />')
    expect(environment).not.toContain('effectiveEntries')
    expect(environment).not.toContain('sourceEntries')
  })

  it('keeps Admin Console enablement read-only in Identity settings', () => {
    const identity = source('src/platform-ops/pages/platform/settings/IdentitySettings.vue')

    expect(identity).toContain('v-model="form.platform_ops_enabled"')
    expect(identity).toMatch(/v-model="form\.platform_ops_enabled"[\s\S]*?\bdisabled\b/)
    expect(identity).not.toContain('if (disablesPlatformOps.value)')
    expect(identity).not.toContain("body.confirm_disable = 'DISABLE'")
  })
})
