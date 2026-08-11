import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'


const usagePage = readFileSync(
  resolve(process.cwd(), 'src/pages/insight/InsightUsage.vue'),
  'utf8',
)


describe('Insight usage refresh semantics', () => {
  it('reloads the HFL ledger without claiming that upstream data was synchronized', () => {
    expect(usagePage).toContain('@click="loadUsage"')
    expect(usagePage).toContain(':disabled="loading"')
    expect(usagePage).toContain("'is-spinning': loading")
    expect(usagePage).not.toContain('Usage refreshed.')
    expect(usagePage).not.toContain('ElMessage.success')
  })

  it('shows persisted freshness and preserves unknown cost semantics', () => {
    expect(usagePage).toContain('usage.value?.data_freshness')
    expect(usagePage).toContain('updates automatically')
    expect(usagePage).toContain("if (value == null) return 'Unavailable'")
    expect(usagePage).toContain('costMode ? row.total_cost')
  })
})
