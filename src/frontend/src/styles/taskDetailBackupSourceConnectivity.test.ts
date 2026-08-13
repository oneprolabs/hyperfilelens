import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const files = [
  'src/pages/ops/Tasks.vue',
  'src/pages/protection/components/TaskDetailDrawer.vue',
  'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue',
]

describe('task detail backup source resource columns', () => {
  it.each(files)('%s uses Connectivity and source availability', (file) => {
    const source = readFileSync(resolve(process.cwd(), file), 'utf8')
    const resourcesTab = source.slice(source.indexOf('<ElTabPane name="resources">'))

    expect(resourcesTab).toContain('protection.sourceResources.colConnectivity')
    expect(resourcesTab).toContain('row.availability')
    expect(resourcesTab).not.toContain('row.flowSource.availability')
  })

  it.each([
    'src/pages/ops/Tasks.vue',
    'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue',
  ])('%s never falls back to lifecycle status for Backup Source connectivity', (file) => {
    const source = readFileSync(resolve(process.cwd(), file), 'utf8')
    const resourcesTab = source.slice(source.indexOf('<ElTabPane name="resources">'))

    expect(resourcesTab).toContain("v-else-if=\"selectedResourceType !== 'backup_source' && row.status\"")
  })
})
