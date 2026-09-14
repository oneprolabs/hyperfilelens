import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8')

describe('action dropdown presentation', () => {
  const actions = source('src/styles/list-page-ui.css')
  const popper = source('src/styles/element-plus-popper.css')
  const table = source('src/styles/element-plus-table.css')
  const index = source('src/index.css')
  const appShell = source('src/app/layout/AppShell.vue')
  const platformTheme = source('src/platform-ops/composables/applyThemeVars.ts')

  it('scopes action item hover and danger states', () => {
    expect(actions).toMatch(/\.hfl-actions-dropdown \.el-dropdown-menu__item:not\(\.is-disabled\):hover/)
    expect(actions).toMatch(/\.hfl-actions-dropdown \.el-dropdown-menu__item--danger:not\(\.is-disabled\)/)
    expect(table).not.toMatch(/\.el-dropdown-menu__item:not\(\.is-disabled\):hover\s*{[^}]*color:\s*#ffffff/s)
  })

  it('matches the reference menu spacing and viewport guard', () => {
    expect(popper).toMatch(/\.hfl-actions-dropdown\.el-popper \.el-dropdown-menu\s*{[^}]*width:\s*max-content;[^}]*min-width:\s*160px;[^}]*max-width:\s*calc\(100vw - 24px\);[^}]*padding:\s*5px/s)
    expect(actions).toMatch(/\.hfl-actions-dropdown \.el-dropdown-menu__item\s*{[^}]*min-height:\s*34px;[^}]*padding:\s*0 9px;[^}]*border-radius:\s*6px/s)
    expect(popper).toMatch(/\.hfl-actions-dropdown \.el-dropdown-menu__item--divided\s*{[^}]*margin-top:\s*11px\s*!important;[^}]*border-top:\s*0;/s)
    expect(popper).toMatch(/\.hfl-actions-dropdown \.el-dropdown-menu__item--divided::before\s*{[^}]*top:\s*-6px;[^}]*right:\s*4px;[^}]*left:\s*4px;[^}]*height:\s*1px;/s)
    expect(actions).not.toMatch(/\.hfl-actions-dropdown \.el-dropdown-menu\s*{[^}]*gap:/s)
  })

  it('scopes the reference primary hover to action toolbars', () => {
    expect(actions).toMatch(/\.hfl-list-toolbar \.el-button--primary:not\(\.is-disabled\):not\(:disabled\)\s*{[\s\S]*?background:\s*var\(--hfl-action-primary-bg, #5B4BE1\)/)
    expect(actions).toMatch(/\.hfl-list-toolbar \.el-button--primary[^{}]*:hover[\s\S]*?background:\s*var\(--hfl-action-primary-hover, #4E3FD4\)/)
    expect(actions).toMatch(/background-image:\s*none\s*!important/)
  })

  it('uses the darker reference hover color for every primary button', () => {
    expect(index).toMatch(/--color-primary-hover-gradient-start:\s*#4E3FD4/)
    expect(index).toMatch(/--color-primary-hover-gradient-end:\s*#4E3FD4/)
    expect(index).toMatch(/--color-primary-hover:\s*#4E3FD4/)
    expect(appShell).not.toContain("--color-primary-hover-gradient-start', '#8876F5")
    expect(appShell).not.toContain("--color-primary-hover-gradient-end', '#7664FA")
    expect(appShell.match(/--color-primary-hover-gradient-start', '#4E3FD4'/g)).toHaveLength(3)
    expect(appShell.match(/--color-primary-hover-gradient-end', '#4E3FD4'/g)).toHaveLength(3)
    expect(platformTheme.match(/--color-primary-hover-gradient-start', '#4E3FD4'/g)).toHaveLength(3)
    expect(platformTheme.match(/--color-primary-hover-gradient-end', '#4E3FD4'/g)).toHaveLength(3)
  })

  it('uses muted solid primary disabled states in every runtime theme', () => {
    expect(index).toMatch(/--color-primary-disabled-bg:\s*color-mix\(in srgb, var\(--color-primary\) 80%, var\(--color-card-bg\)\)/)
    expect(index).toMatch(/--color-primary-disabled-border:\s*color-mix\(in srgb, var\(--color-primary\) 74%, var\(--color-card-bg\)\)/)
    expect(index).toMatch(/--color-primary-disabled-text:\s*color-mix\(in srgb, #FFFFFF 70%, var\(--color-primary\)\)/)
    expect(index).toMatch(/\.el-button--primary[^{}]*\.is-disabled[\s\S]*?background-image:\s*none\s*!important/)
    expect(appShell.match(/--color-primary-disabled-bg', 'color-mix\(in srgb, var\(--color-primary\) 80%, var\(--color-card-bg\)\)'/g)).toHaveLength(2)
    expect(platformTheme.match(/--color-primary-disabled-bg', 'color-mix\(in srgb, var\(--color-primary\) 80%, var\(--color-card-bg\)\)'/g)).toHaveLength(2)
  })

  it('keeps selection summaries as accessible theme-aware pills', () => {
    expect(actions).toMatch(/\.hfl-list-footer__selected\s*{[^}]*border-radius:\s*var\(--radius-full/s)
    expect(actions).toMatch(/--hfl-selection-summary-text:\s*var\(--color-brand-violet-soft\)/)
  })
})
