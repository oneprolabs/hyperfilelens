import { describe, expect, it, vi } from 'vitest'
import type { BackupConfig } from '../../../lib/protectionBackupConfigApi'
import type { BackupPolicy, FileFilterRule } from '../../../lib/protectionPolicyApi'
import { loadMissingBindingDetails } from './bindingDetails'

function config(policyId: number | null, filterId: number | null) {
  return { backup_policy_id: policyId, file_filter_rule_id: filterId } as BackupConfig
}

describe('loadMissingBindingDetails', () => {
  it('loads each missing bound policy and filter once, including schedule and rule details', async () => {
    const policy = {
      id: 4,
      name: 'jlb-policy',
      schedule: { enabled: true, mode: 'interval', interval_unit: 'minute', interval_value: 5 },
    } as BackupPolicy
    const filter = { id: 2, name: 'Source files', ignore_patterns: '*.tmp' } as FileFilterRule
    const getPolicy = vi.fn().mockResolvedValue(policy)
    const getFilter = vi.fn().mockResolvedValue(filter)

    const details = await loadMissingBindingDetails(
      [config(4, 2), config(4, 2), config(null, null)],
      new Map(),
      new Map(),
      getPolicy,
      getFilter,
    )

    expect(getPolicy).toHaveBeenCalledExactlyOnceWith(4)
    expect(getFilter).toHaveBeenCalledExactlyOnceWith(2)
    expect(details).toEqual({ policies: [policy], filters: [filter] })
  })

  it('does not request unbound or already cached details', async () => {
    const getPolicy = vi.fn()
    const getFilter = vi.fn()
    const details = await loadMissingBindingDetails(
      [config(null, null), config(4, 2)],
      new Map([[4, { id: 4 } as BackupPolicy]]),
      new Map([[2, { id: 2 } as FileFilterRule]]),
      getPolicy,
      getFilter,
    )
    expect(details).toEqual({ policies: [], filters: [] })
    expect(getPolicy).not.toHaveBeenCalled()
    expect(getFilter).not.toHaveBeenCalled()
  })

  it('keeps successful details when one request fails so refresh can retry the missing ID', async () => {
    const getPolicy = vi.fn().mockRejectedValue(new Error('temporary failure'))
    const filter = { id: 2, name: 'Source files' } as FileFilterRule
    const getFilter = vi.fn().mockResolvedValue(filter)
    const details = await loadMissingBindingDetails(
      [config(4, 2)],
      new Map(),
      new Map(),
      getPolicy,
      getFilter,
    )
    expect(details).toEqual({ policies: [], filters: [filter] })
    expect(getPolicy).toHaveBeenCalledOnce()
  })
})
