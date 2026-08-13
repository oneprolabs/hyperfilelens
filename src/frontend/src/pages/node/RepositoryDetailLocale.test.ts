import { describe, expect, it } from 'vitest'

import { en } from '../../locales'

describe('repository detail locale', () => {
  it('defines labels used by all DR target detail views', () => {
    expect(en.repositoriesPage.detailFieldRepositoryUsage).toBe('Repository Usage')
    expect(en.repositoriesPage.detailFieldBackingStorage).toBe('Backing Storage')
    expect(en.repositoriesPage.detailFieldPhysicalUsage).toBe('Physical Usage')
    expect(en.repositoriesPage.storageAvailableValue).toBe('{available} available / {total}')
    expect(en.repositoriesPage.modalLead).toBe('Configure repository connection and storage parameters.')
    expect(en.repositoriesPage.estimatedRepositoryData).toBe('Estimated Repository Data')
    expect(en.repositoriesPage.configuredLimit).toBe('Configured Limit')
    expect(en.repositoriesPage.quotaMonitoring).toBe('Quota Monitoring')
    expect(en.repositoriesPage.storageTypeProxyDisk).toBe('Proxy Disk')
  })
})
