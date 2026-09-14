import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/styles/nav-dropdown-panel.css'), 'utf8')

describe('navigation dropdown semantic colors', () => {
  it('uses brand and error tokens instead of legacy blue and magenta accents', () => {
    expect(styles).not.toMatch(/69, 125, 176|#457ab0|#457db0|#93c5fd|#d81b60/i)
    expect(styles).toMatch(/\.nav-dropdown-panel__item:hover\s*{[^}]*background:\s*var\(--color-primary-light\);[^}]*color:\s*var\(--color-primary-hover\)/s)
    expect(styles).toMatch(/\.nav-dropdown-panel__item--danger\s*{[^}]*color:\s*var\(--color-error-text\)/s)
    expect(styles).toMatch(/\.nav-dropdown-panel__item--danger:hover\s*{[^}]*background:\s*var\(--color-error-light\);[^}]*color:\s*var\(--color-error-text\)/s)
  })

  it('clips the panel content to the rounded popover surface', () => {
    expect(styles).toMatch(/\.nav-dropdown-panel\s*{[^}]*border-radius:\s*inherit;[^}]*overflow:\s*hidden;/s)
  })
})
