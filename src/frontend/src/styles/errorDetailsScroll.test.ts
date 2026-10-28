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
  it('constrains the dialog rather than allowing the overlay to scroll', () => {
    expect(rule('.hfl-error-details-overlay .el-overlay-dialog')).toContain('overflow: hidden;')
    const dialog = rule('.hfl-error-details.el-dialog')
    expect(dialog).toContain('display: flex;')
    expect(dialog).toContain('flex-direction: column;')
    expect(dialog).toContain('max-height: calc(var(--app-viewport-height)')
    expect(dialog).toContain('margin: 0;')
  })

  it('keeps header and footer fixed while the body shrinks and scrolls', () => {
    expect(rule('.hfl-error-details .el-dialog__header')).toContain('flex: 0 0 auto;')
    expect(rule('.hfl-error-details .el-dialog__footer')).toContain('flex: 0 0 auto;')
    const body = rule('.hfl-error-details .el-dialog__body')
    expect(body).toContain('min-height: 0;')
    expect(body).toContain('flex: 1 1 auto;')
    expect(body).toContain('overflow-y: auto;')
    expect(component).toContain('body-class="scrollbar"')
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
