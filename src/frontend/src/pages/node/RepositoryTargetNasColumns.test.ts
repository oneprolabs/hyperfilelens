import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')

describe('Target NAS repository columns', () => {
  it('keeps the unused Connectivity label and help icon on one line', () => {
    expect(page).toContain('class="repository-connectivity-tag"')
    expect(page).toContain('class="repository-connectivity-content"')
    expect(page).toContain('.repository-connectivity-tag :deep(.el-tag__content)')
    expect(page).toMatch(/\.repository-connectivity-tag[\s\S]*?width: max-content/)
    expect(page).toMatch(/\.repository-connectivity-tag[\s\S]*?white-space: nowrap/)
    expect(page).toMatch(/\.repository-connectivity-content[\s\S]*?display: inline-flex[\s\S]*?flex-wrap: nowrap/)
    expect(page).toMatch(/\.repository-connectivity-help[\s\S]*?flex: 0 0 auto/)
  })

  it('balances Target NAS widths without hiding stacked values', () => {
    expect(page).toContain(":min-width=\"activeTab === 'nas' ? 152 : activeTab === 's3' ? 137 : 190\"")
    expect(page).toContain('width="119"')
    expect(page).toContain('min-width="247"')
    expect(page).toContain(":min-width=\"activeTab === 'proxy_fs' ? 144 : 158\"")
    expect(page).toContain(":min-width=\"activeTab === 's3' ? 189 : activeTab === 'nas' ? 194 : 228\"")
    expect(page).toContain(":width=\"activeTab === 'nas' ? 142 : activeTab === 's3' ? 110 : 116\"")
    expect(page).toContain(":min-width=\"activeTab === 'nas' || activeTab === 's3' ? 154 : activeTab === 'proxy_fs' ? 170 : createdAtColumnMinWidth\"")
    expect(page).toContain('class="table-stack-cell"')
    expect(page).toContain('class="table-stack-cell__secondary"')
  })
})
