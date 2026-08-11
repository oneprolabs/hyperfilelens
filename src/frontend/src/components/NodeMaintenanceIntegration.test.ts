import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

describe('node maintenance UI integration', () => {
  it.each([
    'src/components/HostSourceDetailDrawer.vue',
    'src/components/ProxyNodeDetailDrawer.vue',
    'src/pages/insight/InsightGatewayDetailDrawer.vue',
  ])('adds Maintenance to the existing detail drawer: %s', (path) => {
    const drawer = source(path)

    expect(drawer).toContain("t('nodeLifecycle.maintenance')")
    expect(drawer).toContain('<NodeMaintenancePanel')
  })

  it('distinguishes tenant and Admin gateway maintenance scope', () => {
    const list = source('src/pages/insight/InsightDataGateways.vue')
    const drawer = source('src/pages/insight/InsightGatewayDetailDrawer.vue')

    expect(list).toContain(":gateway-scope=\"isPlatformEngine ? 'platform' : 'user'\"")
    expect(list).toContain("t('nodeLifecycle.maintenanceCommands')")
    expect(drawer).toContain("getGatewayNode(id, props.gatewayScope === 'platform' ? 'platform' : 'tenant'")
  })

  it('keeps install out of the maintenance command tabs', () => {
    const wizard = source('src/components/NodeLifecycleWizard.vue')

    expect(wizard).toContain("if (props.maintenanceOnly) return ['upgrade', 'uninstall', 'service']")
    expect(wizard).toContain('const purgeAll = ref(false)')
    expect(wizard).toContain('fetchNodeMaintenanceRelease')
    expect(wizard).toContain("scope: platformGateway ? 'platform' : 'tenant'")
    expect(wizard).not.toContain("note: 'upgrade:platform-gateway'")
  })
})
