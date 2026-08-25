import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(__dirname, 'Nodes.vue'), 'utf8')

describe('proxy node version upgrade hint wiring', () => {
  it('passes compatible release state to the shared version cell', () => {
    expect(source).toContain(':target-version="row.agent_release?.target_version"')
    expect(source).toContain(':update-available="row.agent_release?.update_available"')
    expect(source).toContain('row.agent_release?.upgrade_version_allowed')
    expect(source).toContain('canRemoteAgentUpgrade(row.version, latestAgentVersion.value)')
    expect(source).not.toContain("t('nodesPage.versionUpgradeAvailable')")
  })
})
