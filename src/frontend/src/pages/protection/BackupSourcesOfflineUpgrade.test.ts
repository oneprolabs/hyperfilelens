import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), 'src', relativePath), 'utf8')
}

describe('Source Hosts offline upgrade action', () => {
  const page = source('pages/protection/BackupSources.vue')

  it('disables remote upgrade with a hover and focus explanation', () => {
    expect(page).toContain("node.availability !== 'online' || node.routable === false")
    expect(page).toContain(':disabled="hostUpgradeDisabled"')
    expect(page).toContain('<ElTooltip')
    expect(page).toContain(':content="hostUpgradeDisabledReason"')
    expect(page).toContain(':disabled="!hostUpgradeDisabledReason"')
    expect(page).toContain('popper-class="hfl-tooltip--medium"')
    expect(page).toContain(`:fallback-placements="['left', 'top', 'bottom']"`)
    expect(page).toContain(':tabindex="hostUpgradeDisabledReason ? 0 : undefined"')
    expect(page).toContain(':aria-label="hostUpgradeDisabledReason')
    expect(page).not.toContain('class="source-host-upgrade-action__reason"')
    expect(page).toContain("t('nodeLifecycle.nothingEligibleOffline')")
  })

  it('uses the shared viewport-safe explanatory tooltip width', () => {
    const popperStyles = source('styles/element-plus-popper.css')
    expect(popperStyles).toMatch(/\.el-popper\.hfl-tooltip--medium\s*\{[\s\S]*?width:\s*min\(360px, calc\(100vw - 32px\)\)/)
    expect(popperStyles).toMatch(/\.el-popper\.hfl-tooltip--medium\s*\{[\s\S]*?white-space:\s*normal/)
    expect(popperStyles).toMatch(/\.el-popper\.hfl-tooltip--medium\s*\{[\s\S]*?overflow-wrap:\s*anywhere/)
  })

  it('sends the complete selection to preflight so mixed selections report skipped hosts', () => {
    expect(page).toContain("lifecycleOps.runBatch('upgrade', selected)")
    expect(page).not.toContain("lifecycleOps.runBatch('upgrade', targets)")
  })
})
