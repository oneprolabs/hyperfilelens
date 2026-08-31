import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const component = readFileSync(
  resolve(process.cwd(), 'src/pages/insight/copilot/CopilotContextBar.vue'),
  'utf8',
)

describe('Copilot details dialog overflow', () => {
  it('keeps long conversion details inside one viewport-bounded scroll region', () => {
    const desktopBreakpoint = component.indexOf('@media (min-width: 1024px)')

    expect(component).toContain('class="copilot-details-dialog"')
    expect(desktopBreakpoint).toBeGreaterThan(0)
    expect(component.slice(0, desktopBreakpoint)).not.toContain('.copilot-details-dialog {')
    expect(component.slice(desktopBreakpoint)).toContain(
      '.copilot-details-dialog :deep(.el-dialog__body) { min-height: 0; flex: 1 1 auto; overflow-x: hidden; overflow-y: auto; }',
    )
    expect(component.slice(desktopBreakpoint)).toContain(
      'max-height: calc(var(--app-viewport-height) - var(--app-safe-top) - var(--app-safe-bottom) - 32px)',
    )
  })

  it('retains bounded problem details and wraps long content', () => {
    expect(component).toContain('conversionProblemItems(conversion.value).slice(0, 12)')
    expect(component).toContain('.copilot-details section { min-width: 0; }')
    expect(component).toContain('.copilot-details__problems span { overflow-wrap: anywhere;')
  })
})
