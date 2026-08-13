import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const source = readFileSync(new URL('./Repositories.vue', import.meta.url), 'utf8')

describe('repository list hover exclusions', () => {
  it('disables the table overflow tooltip for the entire Backing Storage column', () => {
    const start = source.indexOf(':label="t(\'repositoriesPage.colBackingStorage\')"')
    const end = source.indexOf('<template #default="{ row }">', start)

    expect(start).toBeGreaterThan(-1)
    expect(source.slice(start, end)).toContain('class-name="hfl-table-no-tooltip"')
  })
})
