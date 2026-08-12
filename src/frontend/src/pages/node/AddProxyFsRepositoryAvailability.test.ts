import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/AddProxyFsRepository.vue'), 'utf8')

describe('Add Local Disk Repository proxy availability', () => {
  it('loads proxy nodes without treating online as a lifecycle status', () => {
    expect(page).toContain("listAllNodes({ role: 'proxy' })")
    expect(page).not.toContain("status: 'online'")
  })

  it('only offers proxy nodes whose availability is online', () => {
    expect(page).toContain("node.role === 'proxy' && node.availability === 'online'")
  })

  it('submits a base directory and previews the managed child path', () => {
    expect(page).toContain('proxy_node_base_dir: proxyNodeDir.value.trim()')
    expect(page).toContain('hfl-repo-<repository-id>')
    expect(page).not.toContain('proxy_node_dir: proxyNodeDir.value.trim()')
  })

  it('uses localized repository capacity labels', () => {
    const locale = readFileSync(resolve(process.cwd(), 'src/locales/en.ts'), 'utf8')
    expect(locale).toContain("noConfiguredLimit: 'No configured limit'")
    expect(locale).toContain("colRepositoryUsage: 'Repository Usage'")
    expect(locale).toContain("colBackingStorage: 'Backing Storage'")
  })
})
