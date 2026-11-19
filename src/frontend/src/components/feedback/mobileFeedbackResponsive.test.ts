import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const toastViewport = readFileSync(resolve(process.cwd(), 'src/components/feedback/HflToastViewport.vue'), 'utf8')
const toastItem = readFileSync(resolve(process.cwd(), 'src/components/feedback/HflToastItem.vue'), 'utf8')
const errorDetails = readFileSync(resolve(process.cwd(), 'src/components/feedback/HflErrorDetailsDialog.vue'), 'utf8')

describe('mobile feedback presentation', () => {
  it('keeps desktop toast geometry and uses header-safe mobile positioning', () => {
    expect(toastViewport).toMatch(/\.hfl-toast-viewport\s*{[^}]*top:\s*68px;/s)
    expect(toastViewport).toMatch(/@media \(max-width: 640px\)[\s\S]*?top:\s*calc\(var\(--app-header-height\) \+ 8px\);/)
    expect(toastViewport).toContain('right: max(16px, var(--app-safe-right))')
    expect(toastViewport).toContain('left: max(16px, var(--app-safe-left))')
  })

  it('compacts only mobile single-message toasts', () => {
    expect(toastItem).toMatch(/\.hfl-toast\s*{[^}]*width:\s*372px;/s)
    expect(toastItem).toMatch(/@media \(max-width: 640px\)[\s\S]*?\.hfl-toast--compact\s*{[^}]*padding:\s*4px 0 4px 8px;/)
    expect(toastItem).toMatch(/\.hfl-toast--compact \.hfl-toast__progress\s*{[^}]*display:\s*none;/s)
    expect(toastItem).toMatch(/@media \(max-width: 640px\)[\s\S]*?\.hfl-toast__close\s*{[^}]*width:\s*44px;[^}]*height:\s*44px;/)
  })

  it('keeps error details in the standard responsive drawer with collapsed technical data', () => {
    expect(errorDetails).toContain('direction="rtl"')
    expect(errorDetails).toContain(':size="drawerSize"')
    expect(errorDetails).toContain('class="hfl-error-details__section hfl-error-details__technical-disclosure"')
    expect(errorDetails).not.toMatch(/\.hfl-error-details\.el-drawer\s*{/)
    expect(errorDetails).toMatch(/\.hfl-error-details__technical-disclosure\s*{/s)
  })
})
