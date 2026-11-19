import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const component = readFileSync(resolve(process.cwd(), 'src/components/feedback/HflErrorDetailsDialog.vue'), 'utf8')
const styles = component.slice(component.indexOf('<style>'))

function rule(selector: string) {
  const start = styles.indexOf(`${selector} {`)
  expect(start).toBeGreaterThanOrEqual(0)
  return styles.slice(start, styles.indexOf('}', start) + 1)
}

describe('error details scroll ownership', () => {
  it('uses the shared non-tabbed detail body and keeps actions in the drawer footer', () => {
    expect(component).toContain('class="hfl-detail-drawer hfl-error-details"')
    expect(component).toContain('class="hfl-detail-drawer__body scrollbar"')
    expect(component).toContain('#footer')
    expect(component).toContain('class="el-drawer__footer-main ')
    expect(component).toContain('class="el-drawer__footer-actions ')
    expect(styles).not.toMatch(/\.hfl-error-details\s+\.el-drawer__(header|body|footer)\b/)
  })

  it('removes nested entity scrolling on narrow or short viewports', () => {
    const responsive = styles.slice(styles.indexOf('@media (max-width: 640px), (max-height: 600px)'))
    const entities = responsive.slice(0, responsive.indexOf('}') + 1)
    expect(entities).toContain('max-height: none;')
    expect(entities).toContain('overflow: visible;')
    expect(entities).toContain('scrollbar-gutter: auto;')
    expect(rule('.hfl-error-details__entities')).toContain('overflow-y: auto;')
  })
})
