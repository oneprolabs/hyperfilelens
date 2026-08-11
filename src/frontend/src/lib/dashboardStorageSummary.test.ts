// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { summarizeStorage, topRepos } from './dashboardApi'

describe('dashboard storage capacity summary', () => {
  it('counts a shared physical storage pool once across repositories', () => {
    const summary = summarizeStorage([
      {
        id: 1,
        name: 'repo-a',
        repo_type: 'proxy_fs',
        estimated_usage_bytes: 100,
        storage_pool_key: 'proxy:7:device:8:1',
        storage_total_bytes: 1_000,
        storage_used_bytes: 400,
        storage_available_bytes: 600,
      },
      {
        id: 2,
        name: 'repo-b',
        repo_type: 'proxy_fs',
        estimated_usage_bytes: 250,
        storage_pool_key: 'proxy:7:device:8:1',
        storage_total_bytes: 1_000,
        storage_used_bytes: 400,
        storage_available_bytes: 600,
      },
    ])

    expect(summary.usedBytes).toBe(350)
    expect(summary.storageUsedBytes).toBe(400)
    expect(summary.capacityBytes).toBe(1_000)
    expect(summary.availableBytes).toBe(600)
    expect(summary.coveredRepoCount).toBe(2)
    expect(summary.capacityRepoCount).toBe(2)
    expect(summary.pools).toHaveLength(1)
  })

  it('excludes repositories without physical metrics from the storage denominator', () => {
    const summary = summarizeStorage([
      {
        id: 1,
        name: 'known',
        repo_type: 'proxy_fs',
        storage_pool_key: 'proxy:1:device:1',
        storage_total_bytes: 2_000,
        storage_used_bytes: 500,
        storage_available_bytes: 1_500,
      },
      {
        id: 2,
        name: 'pending',
        repo_type: 'nas',
        capacity_probe_status: 'pending',
        config: { quota_gb: 20 },
        capacity_bytes: 20 * 1024 ** 3,
      },
    ])

    expect(summary.capacityBytes).toBe(2_000)
    expect(summary.storageUsedBytes).toBe(500)
    expect(summary.coveredRepoCount).toBe(1)
    expect(summary.capacityRepoCount).toBe(2)
    expect(summary.repoCount).toBe(2)
  })

  it('uses configured limits only for repository usage, never physical storage', () => {
    const repository = {
      id: 9,
      name: 'planned-only',
      repo_type: 'proxy_fs',
      estimated_usage_bytes: 2 * 1024 ** 3,
      capacity_bytes: 100 * 1024 ** 3,
      config: { quota_gb: 20 },
      capacity_probe_status: 'failed',
    }

    const summary = summarizeStorage([repository])
    const [ranked] = topRepos([repository])

    expect(summary.capacityBytes).toBe(0)
    expect(summary.storageUsedBytes).toBe(0)
    expect(ranked.capacityBytes).toBe(20 * 1024 ** 3)
    expect(ranked.pct).toBe(10)
  })

  it('does not treat object storage as missing physical-capacity coverage', () => {
    const summary = summarizeStorage([
      {
        id: 3,
        name: 'object-storage',
        repo_type: 's3',
        estimated_usage_bytes: 512,
      },
    ])

    expect(summary.repoCount).toBe(1)
    expect(summary.capacityRepoCount).toBe(0)
    expect(summary.coveredRepoCount).toBe(0)
    expect(summary.capacityMode).toBe('unlimited')
  })
})
