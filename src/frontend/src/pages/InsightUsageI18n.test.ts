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
  insight: { side: { usage: string }; usage: Record<string, string> }
  platformOps: { nav: { engineUsage: string } }
}

describe('Insight usage localization', () => {
  it('uses the product term for consumption rather than instructions', () => {
    expect(en.insight.side.usage).toBe('Usage')
    expect(zhHans.insight.side.usage).toBe('用量')
    expect(zhHans.platformOps.nav.engineUsage).toBe('用量')
  })

  it('routes every primary page surface through the locale catalog', () => {
    for (const key of [
      'dateRange',
      'totalCost',
      'aiCalls',
      'totalTokens',
      'questions',
      'usageTrend',
      'tokenUsage',
      'usageByBackupSource',
      'questionHistory',
      'searchPlaceholder',
      'statusCompleted',
    ]) {
      expect(usagePage).toContain(`t('insight.usage.${key}')`)
      expect(zhHans.insight.usage[key]).toBeTruthy()
    }
  })

  it('formats dates, numbers, and currency with the active locale', () => {
    expect(usagePage).toContain('const { t, locale } = useI18n()')
    expect(usagePage).toContain("locale.value === 'en' ? 'en-US' : locale.value")
    expect(usagePage).toContain('Intl.NumberFormat(intlLocale.value')
    expect(usagePage).toContain('Intl.DateTimeFormat(intlLocale.value')
  })
})
