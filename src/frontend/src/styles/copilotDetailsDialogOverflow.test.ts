import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const component = readFileSync(
  resolve(process.cwd(), 'src/pages/insight/copilot/CopilotContextBar.vue'),
  'utf8',
)

describe('Copilot details dialog overflow', () => {
  it('keeps long conversion details inside one viewport-bounded scroll region', () => {
    expect(component).toContain('class="hfl-flow-action-dialog hfl-flow-action-dialog--form copilot-details-dialog"')
    expect(component).toContain('align-center')
    expect(component).not.toContain('margin: calc(var(--app-safe-top) + 16px)')
    expect(component).toContain('max-height: calc(var(--app-viewport-height, 100vh)')
    expect(component).toContain('min-height: 0; flex: 1 1 auto; overflow-x: hidden; overflow-y: auto;')
  })

  it('retains bounded problem details and wraps long content', () => {
    expect(component).toContain('const problemPreviewLimit = 12')
    expect(component).toContain('allProblemItems.value.slice(0, problemPreviewLimit)')
    expect(component).toContain('.copilot-details section { min-width: 0; }')
    expect(component).toContain('.copilot-details__problem-file { overflow-wrap: anywhere;')
    expect(component).not.toContain('<table')
    expect(component).not.toContain('hfl-detail-card__indicator')
    expect(component).toContain('.copilot-details__problem-reason { overflow-wrap: anywhere;')
  })

  it('uses the same ordinary border token as Share Q&A without modifying shared cards', () => {
    const share = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotShareDialog.vue'), 'utf8')
    expect(component).toContain('.copilot-details .hfl-detail-section { border-color: var(--color-border); }')
    expect(share).toMatch(/copilot-share-dialog__preview-content\s*\{[^}]*border: 1px solid var\(--color-border\)/)
  })

  it('gives metadata labels and values more space while keeping narrow-screen values readable', () => {
    expect(component).toMatch(/\.copilot-details dl\s*\{[^}]*grid-template-columns: minmax\(144px, 38%\) minmax\(0, 1fr\);[^}]*gap: 24px;/)
    expect(component).toMatch(/@media \(max-width: 479px\)\s*\{\s*\.copilot-details dl\s*\{[^}]*grid-template-columns: 80px minmax\(0, 1fr\); gap: 16px;/)
  })

  it('matches Share Q&A and analysis settings width, centering, spacing and typography', () => {
    const share = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotShareDialog.vue'), 'utf8')
    const settings = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotExecutionSettingsDialog.vue'), 'utf8')
    for (const dialog of [component, share, settings]) {
      expect(dialog).toContain('width="min(680px, calc(100vw - 32px))"')
      expect(dialog).toContain('align-center')
      expect(dialog).toContain("import '../../../components/backupSourceFlowActionDialog.css'")
    }
    for (const declaration of [
      'padding: 20px 24px 16px',
      'padding: 16px 24px 18px',
      'padding: 16px 16px 12px',
      'padding: 14px 16px 16px',
    ]) {
      expect(component).toContain(declaration)
      expect(share).toContain(declaration)
    }
    expect(component).toContain('font-size: var(--hfl-flow-action-font-size)')
    expect(component).toContain('line-height: var(--hfl-flow-action-line-height)')
  })
})
