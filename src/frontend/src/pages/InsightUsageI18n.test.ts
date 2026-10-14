import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'

const usagePage = readFileSync(
  resolve(process.cwd(), 'src/pages/insight/InsightUsage.vue'),
  'utf8',
)
const zhHans = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
)) as {
  insight: { side: { requestHistory: string }; usage: Record<string, string> }
  platformOps: { nav: { engineUsage: string } }
}

describe('Insight usage localization', () => {
  it('uses the product term for consumption rather than instructions', () => {
    const requestHistory = String.fromCodePoint(0x8bf7, 0x6c42, 0x8bb0, 0x5f55)
    const aiUsage = `AI ${String.fromCodePoint(0x7528, 0x91cf)}`
    expect(en.insight.side.requestHistory).toBe('Request History')
    expect(zhHans.insight.side.requestHistory).toBe(requestHistory)
    expect(zhHans.platformOps.nav.engineUsage).toBe(aiUsage)
  })

  it('routes the history and its controls through the locale catalog', () => {
    for (const key of [
      'dateRange',
      'question',
      'chat',
      'backupSource',
      'status',
      'time',
      'searchPlaceholder',
      'statusCompleted',
    ]) {
      expect(usagePage).toContain(`t('insight.usage.${key}')`)
      expect(zhHans.insight.usage[key]).toBeTruthy()
    }
  })

  it('removes dashboard sections and chart dependencies rather than hiding them', () => {
    for (const key of ['totalCost', 'questions', 'usageTrend', 'tokenUsage', 'usageByBackupSource']) {
      expect(usagePage).not.toContain(`t('insight.usage.${key}')`)
    }
    for (const removed of ['echarts', 'VChart', 'chartOption', 'tokenBreakdown', 'usage-summary-grid', 'usage-breakdown-grid']) {
      expect(usagePage).not.toContain(removed)
    }
    expect(usagePage).toContain('class="hfl-list-table usage-history-table"')
    expect(usagePage).toContain(':data="usage?.results || []"')
    expect(usagePage).toContain(':total="usage.total"')
  })

  it('uses the shared full local datetime format for list timestamps', () => {
    expect(usagePage).toContain("import { formatLocalDateTime } from '../../lib/dateTime'")
    expect(usagePage).toContain('formatLocalDateTime(row.time)')
    expect(usagePage).not.toContain('formatShortDateTime')
  })
})
