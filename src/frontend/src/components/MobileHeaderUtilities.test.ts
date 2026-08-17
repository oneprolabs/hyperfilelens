import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

const appShell = source('src/app/layout/AppShell.vue')
const topNav = source('src/app/layout/TopNav.vue')
const drawer = source('src/components/MobileNavigationDrawer.vue')
const languageSwitcher = source('src/components/LanguageSwitcher.vue')
const userMenu = source('src/components/NavUserMenu.vue')
const login = source('src/pages/auth/Login.vue')
const register = source('src/pages/auth/Register.vue')
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

  it('keeps language labels readable and groups the desktop selector with user utilities', () => {
    const notificationsIndex = topNav.indexOf('<NavNotificationPopover />')
    const languageIndex = topNav.indexOf('<LanguageSwitcher variant="navigation" />')
    const userIndex = topNav.indexOf('<NavUserMenu />')

    expect(notificationsIndex).toBeGreaterThan(-1)
    expect(languageIndex).toBeGreaterThan(notificationsIndex)
    expect(userIndex).toBeGreaterThan(languageIndex)
    expect(languageSwitcher).toMatch(
      /\.language-switcher__current\s*{[\s\S]*?line-height:\s*1\.4/,
    )
    expect(languageSwitcher).toMatch(
      /\.language-switcher--navigation \.language-switcher__trigger,[\s\S]*?border-color:\s*var\(--tz-border/,
    )
    expect(login).toMatch(
      /\.login-box-title\s*{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto;[\s\S]*?align-items:\s*center/,
    )
    expect(register).toMatch(
      /\.register-box-title\s*{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto;[\s\S]*?align-items:\s*center/,
    )
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
