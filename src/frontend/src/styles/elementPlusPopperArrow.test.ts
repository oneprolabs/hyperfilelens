import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8')
const sharedStyles = source('src/styles/element-plus-popper.css')
const tableStyles = source('src/styles/element-plus-table.css')
const datePickerStyles = source('src/styles/element-plus-date-picker.css')
const navigationStyles = source('src/styles/nav-dropdown-panel.css')
const languageSwitcher = source('src/components/LanguageSwitcher.vue')

describe('Element Plus popper arrows', () => {
  it('does not clip arrows at the popper root', () => {
    expect(sharedStyles).toMatch(/\.el-popper\s*{[^}]*overflow:\s*visible;/s)
    expect(tableStyles).not.toMatch(
      /\.el-popper:not\(\.hfl-pagination-size-popper\)\s*{[^}]*overflow:\s*hidden;/s,
    )
    expect(datePickerStyles).not.toMatch(
      /\.el-picker__popper\.el-popper\s*{[^}]*overflow:\s*hidden;/s,
    )
    expect(navigationStyles).not.toMatch(
      /\.nav-dropdown-popover\.el-popper\s*{[^}]*overflow:\s*hidden;/s,
    )
    expect(languageSwitcher).not.toMatch(
      /\.hfl-language-switcher-popper\.el-popper\s*{[^}]*overflow:\s*hidden;/s,
    )
  })

  it('keeps clipping on inner menu and panel content', () => {
    expect(sharedStyles).toMatch(
      /\.el-dropdown-menu,[\s\S]*?\.el-table-filter__content\s*{[^}]*overflow:\s*hidden;/,
    )
    expect(navigationStyles).toMatch(/\.nav-dropdown-panel\s*{[^}]*overflow:\s*hidden;/s)
  })

  it('clips the bordered arrow to its exposed triangular half', () => {
    expect(sharedStyles).toMatch(
      /\[data-popper-placement\^='bottom'\][\s\S]*?clip-path:\s*inset\(-3px 0 5px 0\)/,
    )
    expect(sharedStyles).toMatch(
      /\[data-popper-placement\^='top'\][\s\S]*?clip-path:\s*inset\(5px 0 -3px 0\)/,
    )
    expect(languageSwitcher).toMatch(
      /\.hfl-language-switcher-popper\.el-popper \.el-popper__arrow::before\s*{[^}]*background:\s*#272633\s*!important;/s,
    )
  })
})
