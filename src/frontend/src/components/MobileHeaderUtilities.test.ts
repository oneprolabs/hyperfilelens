import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

const appShell = source('src/app/layout/AppShell.vue')
const topNav = source('src/app/layout/TopNav.vue')
const drawer = source('src/components/MobileNavigationDrawer.vue')
const userMenu = source('src/components/NavUserMenu.vue')
const dropdownStyles = source('src/styles/nav-dropdown-panel.css')
const locale = source('src/locales/en.ts')

describe('mobile header utilities', () => {
  it('shares deploy profile state from the app shell and one language selector', () => {
    expect(appShell).toContain('async function refreshHeaderProfile()')
    expect(appShell).toContain(':admin-console-href="adminConsoleHref"')
    expect(appShell).toContain(':timezone-offset-display="timezoneOffsetDisplay"')
    expect(topNav).toContain('<LanguageSwitcher variant="navigation" />')
    expect(drawer).toContain('variant="mobile"')
    expect(topNav).not.toContain('fetchDeployProfile')
  })

  it('keeps hidden desktop utilities reachable from mobile surfaces', () => {
    expect(drawer).toContain('<OrgSwitcher variant="mobile" />')
    expect(drawer).toContain('target="_blank"')
    expect(drawer).toContain("$t('nav.platformOps')")
    expect(drawer).toContain('<LanguageSwitcher')
    expect(drawer).toContain("$t('nav.timezoneLabel')")
    expect(drawer).toContain('{{ timezoneOffsetDisplay }}')
    expect(userMenu).not.toContain('nav-user-timezone')
    expect(locale).toContain("timezoneLabel: 'Time Zone'")
    expect(locale).toContain("languageLabel: 'Language'")
  })

  it('keeps mobile triggers and popovers within narrow viewports', () => {
    expect(userMenu).toMatch(
      /@media \(max-width: 1023\.98px\)[\s\S]*?\.nav-user-trigger\s*{[\s\S]*?min-height:\s*44px/,
    )
    expect(dropdownStyles).toMatch(
      /\.nav-dropdown-popover\.el-popper\s*{[\s\S]*?max-width:\s*calc\(100vw - 24px\)/,
    )
  })
})
