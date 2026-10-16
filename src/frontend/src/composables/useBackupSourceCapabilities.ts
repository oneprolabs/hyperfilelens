import { computed, type Ref } from 'vue'
import { useResourceCapabilities, type ResourceReference } from './useResourceCapabilities'

type Source = { id: string }
type BackupConfig = { id: number; repository_id: number; source_type: string; source_ref_id: number }

/** Prefetch visible sources; retain only explicit selections needed across pages. */
export function useBackupSourceCapabilities(
  sources: Ref<Source[]>,
  configs: Ref<BackupConfig[]>,
  retainedSources?: Ref<Source[]>,
) {
  return useResourceCapabilities(computed(() => {
    const scopedSources = [...sources.value, ...(retainedSources?.value || [])]
    const references: ResourceReference[] = []
    const sourceIds = new Set(scopedSources.map((source) => source.id))
    for (const source of scopedSources) {
      const [kind, rawId] = source.id.split(':')
      const id = Number(rawId)
      if (!['agent', 'nas'].includes(kind) || !Number.isSafeInteger(id) || id <= 0) continue
      references.push({ resource_type: kind === 'nas' ? 'source_resource' : 'node', resource_id: id })
    }
    for (const config of configs.value) {
      if (!sourceIds.has(`${config.source_type}:${config.source_ref_id}`)) continue
      references.push({ resource_type: 'backup_config', resource_id: config.id })
      references.push({ resource_type: 'repository', resource_id: config.repository_id })
    }
    return references
  }))
}
