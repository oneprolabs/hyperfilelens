import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function page(path: string) {
  return readFileSync(resolve(process.cwd(), 'src', path), 'utf8')
}

describe('Advanced force option integration boundaries', () => {
  it.each([
    'components/BackupSourceUnregisterDialogBody.vue',
    'pages/node/Nodes.vue',
    'pages/node/Repositories.vue',
    'pages/insight/InsightDataGateways.vue',
  ])('shares the source deregistration force option card in %s', (path) => {
    const source = page(path)
    expect(source).toContain('<DangerForceOption')
    expect(source).not.toMatch(/\.(node|repo|gateway)-delete-force-option\s*{/)
  })
  it('keeps Proxy force availability and binding warnings outside the disclosure', () => {
    const source = page('pages/node/Nodes.vue')
    const start = source.indexOf('<DangerAdvancedOptions')
    const end = source.indexOf('</DangerAdvancedOptions>', start)
    const advanced = source.slice(start, end)
    expect(advanced).toContain('v-if="isProxyNodesPage"')
    expect(advanced).toContain(':disabled="deleteLoading"')
    expect(source.slice(0, start)).toContain('t(\'common.proxyRemovalLimits\')')
    expect(advanced).toContain('common.proxyForceExplanation')
    expect(advanced).not.toContain('common.proxyRemovalLimits')
    expect(source).toContain(':confirmation-context="pendingDeleteNodes.map')
  })

  it('keeps repository single-target availability and preflight results outside the disclosure', () => {
    const source = page('pages/node/Repositories.vue')
    const start = source.indexOf('<DangerAdvancedOptions')
    const end = source.indexOf('</DangerAdvancedOptions>', start)
    expect(source.slice(start, end)).toContain('v-if="canForceDeleteRepository"')
    expect(source.slice(start, end)).toContain(':disabled="busy"')
    expect(source.slice(start, end)).not.toContain('deletePreflightMessages')
    expect(source.slice(0, start)).toContain('v-if="deletePreflightMessages.length"')
    expect(source).toContain('pendingDeleteRepositories.value.length === 1')
  })

  it('keeps Gateway recoverable errors outside the disclosure without locking confirmation', () => {
    const source = page('pages/insight/InsightDataGateways.vue')
    const dialog = source.slice(source.indexOf('v-model="deleteOpen"'))
    expect(dialog.indexOf('gateway-delete-errors')).toBeLessThan(dialog.indexOf('<DangerAdvancedOptions'))
    expect(dialog.indexOf('common.gatewayRemovalLimits')).toBeLessThan(dialog.indexOf('<DangerAdvancedOptions'))
    expect(dialog).toContain('common.gatewayForceExplanation')
    expect(dialog).not.toContain(':error-text=')
    expect(dialog).toContain(':disabled="deleteLoading"')
    expect(source).not.toContain('deleteForce.value = true')
  })
})

describe('Gateway deletion presentation', () => {
  it('uses a Gateway-specific title and shared keyword confirmation', () => {
    const source = page('pages/insight/InsightDataGateways.vue')
    const dialog = source.slice(source.indexOf('v-model="deleteOpen"'))
    expect(dialog).toContain("t('insight.dataGateway.deleteTitle')")
    expect(dialog).toContain('common.dangerConfirmKeywordHint')
    expect(dialog).toContain('confirm-keyword-placeholder')
    expect(dialog).toContain('FORCE DELETE')
  })
})
