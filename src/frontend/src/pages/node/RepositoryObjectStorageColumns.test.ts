import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')

describe('Object Storage repository columns', () => {
  it('uses the compact Object Storage width allocation', () => {
    expect(page).toContain(":min-width=\"activeTab === 'nas' ? 152 : activeTab === 's3' ? 137 : 190\"")
    expect(page).toMatch(/colS3ObjectPrefix[\s\S]*?min-width="113"/)
    expect(page).toContain(":min-width=\"activeTab === 's3' ? 189 : activeTab === 'nas' ? 194 : 228\"")
    expect(page).toContain(":width=\"activeTab === 'nas' ? 142 : activeTab === 's3' ? 110 : 116\"")
    expect(page).toContain(":min-width=\"activeTab === 'nas' || activeTab === 's3' ? 154 : activeTab === 'proxy_fs' ? 170 : createdAtColumnMinWidth\"")
  })
})
