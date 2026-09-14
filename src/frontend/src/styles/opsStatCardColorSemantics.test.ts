import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8')

const tokenValue = (content: string, name: string) => {
  const match = content.match(new RegExp(`--${name}:\\s*([^;]+);`))
  return match?.[1].trim()
}

const opsPageSources = () => readdirSync(resolve(process.cwd(), 'src/pages/ops'))
  .filter((name) => name.endsWith('.vue'))
  .map((name) => [`src/pages/ops/${name}`, source(`src/pages/ops/${name}`)] as const)

const operationsCardConsumers = [
  'src/pages/ops/AlertIncidents.vue',
  'src/pages/ops/AlertPolicies.vue',
  'src/pages/ops/Audit.vue',
  'src/pages/ops/NotificationChannels.vue',
  'src/pages/ops/NotificationRecords.vue',
  'src/pages/ops/Tasks.vue',
  'src/components/monitor/SystemMonitorDashboard.vue',
]

describe('Operations statistics card color semantics', () => {
  it('uses only shared semantic tones in every Operations consumer', () => {
    for (const path of operationsCardConsumers) {
      const content = source(path)
      expect(content, path).not.toMatch(/(?:^|\s):?accent=/)
      expect(content, path).not.toMatch(/(?:^|\s)value-class=/)
    }

    const component = source('src/components/ops/OpsStatCard.vue')
    expect(component).toContain("tone?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'firing' | 'neutral'")
    expect(component).not.toContain('accent?:')
    expect(component).not.toContain('valueClass?:')
  })

  it('keeps alert severity and lifecycle tones distinct', () => {
    const incidents = source('src/pages/ops/AlertIncidents.vue')
    const policies = source('src/pages/ops/AlertPolicies.vue')

    expect(incidents).toContain('tone="danger"')
    expect(incidents).toContain('tone="warning"')
    expect(incidents).toContain('tone="firing"')
    expect(incidents).toContain('tone="info"')
    expect(policies).toContain('tone="primary"')
    expect(policies).toContain('tone="success"')
    expect(policies).toContain('tone="danger"')
  })

  it('keeps task waiting and cancelled states semantically distinct', () => {
    const tasks = source('src/pages/ops/Tasks.vue')

    expect(tasks).toMatch(/status\.pending[\s\S]*?tone="warning"/)
    expect(tasks).toMatch(/status\.cancelled[\s\S]*?tone="neutral"/)
  })

  it('uses success semantics for positive rates', () => {
    const records = source('src/pages/ops/NotificationRecords.vue')
    const channels = source('src/pages/ops/NotificationChannels.vue')

    expect(records).toMatch(/notification\.successRate[\s\S]*?tone="success"/)
    expect(channels).toMatch(/notification\.enabledRate[\s\S]*?tone="success"/)
  })

  it('resolves card and firing tag colors through global tokens', () => {
    const tokens = source('src/index.css')
    const tagStyles = source('src/styles/element-plus-tag.css')
    const cardStyles = source('src/styles/ops-list-ui.css')

    expect(tokens).toContain('--color-firing:')
    expect(tokens).toContain('--color-firing-light:')
    expect(tokens).toContain('--color-firing-border:')
    expect(tokens).toContain('--color-text-inverse:')
    expect(tokens).toContain('--color-primary-border:')
    expect(tokenValue(tokens, 'color-info')).not.toBe(tokenValue(tokens, 'color-primary'))
    expect(tokenValue(tokens, 'color-info-light')).not.toBe(tokenValue(tokens, 'color-primary-light'))
    expect(tokenValue(tokens, 'color-firing')).not.toBe(tokenValue(tokens, 'color-warning'))
    expect(tokens).toMatch(/html\[data-theme='dark'\][\s\S]*--color-success-light:/)
    expect(tokens).toMatch(/html\[data-theme='dark'\][\s\S]*--color-warning-light:/)
    expect(tokens).toMatch(/html\[data-theme='dark'\][\s\S]*--color-firing-light:/)
    expect(tokens).toMatch(/html\[data-theme='dark'\][\s\S]*--color-error-light:/)
    expect(tokens).toMatch(/html\[data-theme='dark'\][\s\S]*--color-info-light:/)
    expect(tagStyles).toContain('.el-tag.hfl-tag--firing')
    expect(tagStyles).toContain('--el-tag-text-color: var(--color-primary)')
    expect(tagStyles).toContain('--el-tag-text-color: var(--color-info-text)')
    expect(cardStyles).toContain('--hfl-ops-stat-accent:')
    expect(cardStyles).toContain('.hfl-ops-stat-card--tone-firing')
    expect(cardStyles).not.toMatch(/\.hfl-ops-stat-card--(?:red|yellow|green|blue|indigo|orange|pink|gray)/)
  })

  it('keeps Operations page and shared Operations styles theme-token based', () => {
    const rawThemeColor = /#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|\b(?:text|bg|border)-(?:slate|gray|red|green|yellow|orange|blue|indigo|pink|emerald|amber)-[0-9]{2,3}\b/i

    for (const [path, content] of opsPageSources()) {
      expect(content, path).not.toMatch(rawThemeColor)
    }

    expect(source('src/styles/ops-list-ui.css')).not.toMatch(rawThemeColor)
  })
})
