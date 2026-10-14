import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'


const usagePage = readFileSync(
  resolve(process.cwd(), 'src/pages/insight/InsightUsage.vue'),
  'utf8',
)


describe('Insight usage history controls', () => {
  it('loads on mount and filter changes without a manual refresh button', () => {
    expect(usagePage).toContain('onMounted(() =>')
    expect(usagePage).toContain('watch(statusFilter,')
    expect(usagePage).toContain('watch(searchQuery,')
    expect(usagePage).not.toContain('usage-icon-button')
    expect(usagePage).not.toContain('RefreshCw')
    expect(usagePage).not.toContain('Usage refreshed.')
    expect(usagePage).not.toContain('ElMessage.success')
  })

  it('removes usage metrics without reintroducing dashboard content', () => {
    expect(usagePage).not.toContain('formatCost(')
    expect(usagePage).not.toContain('formatCompact(')
    expect(usagePage).not.toContain('usageFreshness')
    expect(usagePage).not.toContain('usage.value?.summary')
    expect(usagePage).not.toContain('usage.value?.trend')
  })
})
