import type { BackupConfig } from '../../../lib/protectionBackupConfigApi'
import type { BackupPolicy, FileFilterRule } from '../../../lib/protectionPolicyApi'

export async function loadMissingBindingDetails(
  configs: Array<BackupConfig>,
  policies: ReadonlyMap<number, BackupPolicy>,
  filters: ReadonlyMap<number, FileFilterRule>,
  getPolicy: (id: number) => Promise<BackupPolicy>,
  getFilter: (id: number) => Promise<FileFilterRule>,
): Promise<{ policies: BackupPolicy[]; filters: FileFilterRule[] }> {
  const policyIds = new Set<number>()
  const filterIds = new Set<number>()
  for (const config of configs) {
    if (config.backup_policy_id && !policies.has(config.backup_policy_id)) {
      policyIds.add(config.backup_policy_id)
    }
    if (config.file_filter_rule_id && !filters.has(config.file_filter_rule_id)) {
      filterIds.add(config.file_filter_rule_id)
    }
  }

  // Missing detail must not make a successfully created backup configuration
  // look like a failed create. A later Step 3 refresh can retry these IDs.
  const [policyResults, filterResults] = await Promise.all([
    Promise.allSettled([...policyIds].map((id) => getPolicy(id))),
    Promise.allSettled([...filterIds].map((id) => getFilter(id))),
  ])
  return {
    policies: policyResults.flatMap((result) => result.status === 'fulfilled' ? [result.value] : []),
    filters: filterResults.flatMap((result) => result.status === 'fulfilled' ? [result.value] : []),
  }
}
