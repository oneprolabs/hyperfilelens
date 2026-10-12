import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8')

describe('pagination gutters and wizard scrolling', () => {
  it('lets outer containers own bottom and horizontal gutters on desktop and mobile', () => {
    const css = source('src/styles/list-page-ui.css')
    const footers = [...css.matchAll(/\.hfl-list-footer\s*\{([^}]+)\}/g)].map(match => match[1])
    expect(footers).toHaveLength(2)
    expect(footers[0]).toContain('padding: 16px 0 0;')
    expect(footers[0]).toContain('flex: 0 0 auto;')
    expect(footers[1]).toContain('padding: 12px 0 0;')
  })

  it('uses a bounded wizard container and the same height calculation as other lists', () => {
    const page = source('src/pages/protection/DataProtection.vue')
    expect(page).toContain(':body-fill="inlineEditorMode === null"')
    expect(page).toContain('listTableMaxHeight(zone, visibleFooter, FLOW_TABLE_FOOTER_RESERVE)')
    expect(page).toContain('flowTableZoneObserver?.observe(flowObservedFooter)')
    expect(page).not.toContain('Math.max(280, Math.floor(pageBottom - zoneTop - footerHeight))')
  })
})
