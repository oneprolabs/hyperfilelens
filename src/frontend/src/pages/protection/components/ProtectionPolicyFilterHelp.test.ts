import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const editor = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/ProtectionPolicyEditorForm.vue'),
  'utf8',
)
const guide = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/FileFilterRuleGuide.vue'),
  'utf8',
)
const locale = readFileSync(
  resolve(process.cwd(), 'src/locales/enProtectionPages.ts'),
  'utf8',
)
const popperStyles = readFileSync(
  resolve(process.cwd(), 'src/styles/element-plus-table.css'),
  'utf8',
)

describe('protection filter advanced-setting help', () => {
  it('gives both advanced settings structured help with a readable popover style', () => {
    expect(editor.match(/popper-class="filter-advanced-help-popper"/g)).toHaveLength(2)
    expect(editor.match(/class="filter-advanced-help"/g)).toHaveLength(2)
    expect(editor.match(/class="filter-advanced-help__list"/g)).toHaveLength(2)
    expect(editor).toMatch(/:global\(\.filter-advanced-help-popper\)[\s\S]*?max-width:/)
    expect(editor).toMatch(/\.filter-advanced-help__example[\s\S]*?border-top:/)
  })

  it('inherits the tooltip text color from the global light popper theme', () => {
    expect(popperStyles).toMatch(/\.el-popper\.is-dark\s*\{[^}]*color:[^}]*!important;[^}]*background:[^}]*!important;/s)
    expect(editor).toMatch(/\.filter-advanced-help\s*\{[^}]*color:\s*inherit;/s)
    expect(editor).not.toMatch(/\.filter-advanced-help\s*\{[^}]*color:\s*rgb\(248 250 252\)/s)
  })

  it('explains marker-based cache filtering without requiring a folder list', () => {
    expect(locale).toContain("cacheTitle: 'Skip Marked Cache Folders'")
    expect(guide).toContain('<strong>Skip marked cache folders</strong>')
    expect(editor).toContain("t('protection.policiesPage.cacheTooltipLead')")
    expect(editor).toContain("t('protection.policiesPage.cacheTooltipNamedFolder')")
    expect(editor).toContain("t('protection.policiesPage.cacheTooltipExample')")
    expect(guide).toContain('<code>CACHEDIR.TAG</code>')
    expect(guide).toContain('<code>**/.cache/**</code>')
  })

  it('explains nested filesystem mounts with a concrete path example', () => {
    expect(editor).toContain("t('protection.policiesPage.fsOnlyTooltipExample')")
    expect(editor).toContain("t('protection.policiesPage.fsOnlyTooltipOff')")
    expect(guide).toContain('<code>/data/archive</code>')
    expect(guide).toContain('<code>/data</code>')
  })

  it('does not mention the implementation product in the affected descriptions', () => {
    const affectedLocaleLines = locale
      .split('\n')
      .filter((line) => /cacheTitle|cacheSub|cacheTooltip|filterHelpExampleLabel|fsOnlyTitle|fsOnlyTooltip|fsOnlySub/.test(line))

    expect(affectedLocaleLines.join('\n')).not.toMatch(/kopia/i)
    expect(guide).not.toMatch(/kopia/i)
  })
})
