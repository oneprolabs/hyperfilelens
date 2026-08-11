import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { backupSourceLifecycleDisplay } from '../../lib/backupSourceLifecycleDisplay'
import { en } from '../../locales/en'
import { enProtectionPages } from '../../locales/enProtectionPages'

function source(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), 'src', relativePath), 'utf8')
}

describe('backup source terminology', () => {
  it('defines the lifecycle, connectivity, and registration labels', () => {
    expect(enProtectionPages.sourceResources.colLifecycleStatus).toBe('Lifecycle Status')
    expect(enProtectionPages.sourceResources.lifecycleRegistered).toBe('Registered')
    expect(enProtectionPages.sourceResources.colConnectivity).toBe('Connectivity')
    expect(enProtectionPages.sourceResources.fieldConnectivityUpdatedAt).toBe('Connectivity Updated At')
    expect(enProtectionPages.sourceResources.colRegisteredAt).toBe('Registered At')
  })

  it('maps only the stable lifecycle label into backup-source terminology', () => {
    expect(backupSourceLifecycleDisplay({ labelKey: 'nodeLifecycle.state.active', tagType: 'success' })).toEqual({
      labelKey: 'protection.sourceResources.lifecycleRegistered',
      tagType: 'success',
    })
    expect(backupSourceLifecycleDisplay({ labelKey: 'nodeLifecycle.state.upgrading', tagType: 'info' })).toEqual({
      labelKey: 'nodeLifecycle.state.upgrading',
      tagType: 'info',
    })
  })

  it('uses contextual terminology in source details without changing gateway details', () => {
    const basicPanel = source('components/NodeBasicInfoPanel.vue')
    expect(basicPanel).toContain('useBackupSourceTerminology')
    expect(source('components/HostSourceDetailDrawer.vue')).toContain('use-backup-source-terminology')
    expect(source('components/ProxyNodeDetailDrawer.vue')).toContain('use-backup-source-terminology')
    expect(source('pages/insight/InsightGatewayDetailDrawer.vue')).not.toContain('use-backup-source-terminology')
  })

  it('renames existing related-source columns while retaining NAS Connectivity', () => {
    const policies = source('pages/protection/Policies.vue')
    const repositories = source('pages/node/Repositories.vue')
    expect(en.repositoriesPage.associatedSourceColAvailability).toBe('Connectivity')
    expect(en.repositoriesPage.associatedSourceColNasConnectivity).toBe('NAS Connectivity')
    expect(policies).toContain("protection.sourceResources.colRegisteredAt")
    expect(repositories).toContain("protection.sourceResources.colRegisteredAt")
    expect(repositories).toContain("repositoriesPage.associatedSourceColNasConnectivity")
  })

  it('uses Connectivity for the repository list and every repository detail variant', () => {
    const repositories = source('pages/node/Repositories.vue')
    const detailDrawer = repositories.slice(repositories.indexOf('<ElDrawer'))
    expect(en.repositoriesPage.colAvailability).toBe('Connectivity')
    expect(repositories.split("t('repositoriesPage.colAvailability')")).toHaveLength(5)
    expect(detailDrawer.split("t('repositoriesPage.colAvailability')")).toHaveLength(4)
  })

  it('keeps filter request values while changing their visible labels', () => {
    const page = source('pages/protection/DataProtection.vue')
    expect(enProtectionPages.backupsPage.step3SourceStatus).toBe('Lifecycle Status')
    expect(enProtectionPages.backupsPage.step3Availability).toBe('Connectivity')
    expect(enProtectionPages.backupsPage.step3StatusActive).toBe('Registered')
    expect(page).toContain("source_status: step3SourceStatus.value || undefined")
    expect(page).toContain("availability: step3Availability.value || undefined")
  })
})
