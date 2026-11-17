import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

const appShell = source('src/app/layout/AppShell.vue')
const topNav = source('src/app/layout/TopNav.vue')
const platformOpsShell = source('src/platform-ops/layout/PlatformOpsShell.vue')
const drawer = source('src/components/MobileNavigationDrawer.vue')
const languageSwitcher = source('src/components/LanguageSwitcher.vue')
const userMenu = source('src/components/NavUserMenu.vue')
const orgSwitcher = source('src/components/OrgSwitcher.vue')
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

  it('orders desktop utilities by platform, localization, notifications, and user', () => {
    const adminConsoleIndex = topNav.indexOf('class="platform-ops-entry desktop-navigation-control"')
    const timezoneIndex = topNav.indexOf('class="timezone-display desktop-navigation-control"')
    const notificationsIndex = topNav.indexOf('<NavNotificationPopover />')
    const languageIndex = topNav.indexOf('<LanguageSwitcher variant="navigation" />')
    const userIndex = topNav.indexOf('<NavUserMenu />')

    expect(adminConsoleIndex).toBeGreaterThan(-1)
    expect(timezoneIndex).toBeGreaterThan(adminConsoleIndex)
    expect(languageIndex).toBeGreaterThan(timezoneIndex)
    expect(notificationsIndex).toBeGreaterThan(languageIndex)
    expect(userIndex).toBeGreaterThan(notificationsIndex)
    expect(languageSwitcher).toMatch(
      /\.language-switcher__current\s*{[\s\S]*?line-height:\s*1\.4/,
    )
    expect(languageSwitcher).toMatch(
      /\.language-switcher--navigation \.language-switcher__current\s*{[\s\S]*?line-height:\s*1\.5/,
    )
    expect(languageSwitcher).toMatch(
      /\.language-switcher--navigation \.language-switcher__trigger,[\s\S]*?border-color:\s*var\(--nav-utility-border/,
    )
    expect(languageSwitcher).not.toContain(':title="ariaLabel"')
    expect(languageSwitcher).toContain(':aria-label="ariaLabel"')
    expect(login).toMatch(
      /\.login-box-title\s*{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto;[\s\S]*?align-items:\s*center/,
    )
    expect(register).toMatch(
      /\.register-box-title\s*{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto;[\s\S]*?align-items:\s*center/,
    )
  })

  it('shows the tenant time zone as a quiet read-only status with an accessible label', () => {
    expect(topNav).toContain('<Clock3')
    expect(topNav).toContain(":aria-label=\"`${t('nav.timezone')} ${timezoneOffsetDisplay}`\"")
    expect(topNav).not.toContain(':title="timezoneDisplay"')
    expect(topNav).not.toContain(':title="t(\'nav.platformOps\')"')
    const timezoneStyles = topNav.match(/\.timezone-display\s*{([^}]+)}/)?.[1] || ''
    expect(timezoneStyles).toContain('color: var(--tz-color,')
    expect(timezoneStyles).toContain('background: var(--nav-utility-bg)')
    expect(timezoneStyles).toContain('border: 1px solid var(--nav-utility-border)')
    expect(topNav).toContain('class="timezone-display__value"')
    expect(topNav).toMatch(/\.timezone-display__value\s*{[\s\S]*?line-height:\s*1\.5;/)
    expect(topNav).not.toContain('timezone-display__label')
  })

  it('aligns desktop utility text using matching heights and line boxes', () => {
    expect(topNav).toMatch(/\.timezone-display\s*{[^}]*height:\s*32px;[^}]*box-sizing:\s*border-box;[^}]*font-size:\s*13px;[^}]*line-height:\s*1\.5;/)
    expect(topNav).toMatch(/\.platform-ops-entry\s*{[^}]*box-sizing:\s*border-box;[^}]*height:\s*32px;[^}]*line-height:\s*1\.5;/)
    expect(orgSwitcher).toMatch(/\.org-switcher__trigger\s*{[^}]*box-sizing:\s*border-box;[^}]*height:\s*32px;[^}]*font-size:\s*13px;[^}]*line-height:\s*1\.5;/)
    expect(orgSwitcher).not.toContain(':title="currentName"')
    expect(orgSwitcher).toContain(':aria-label="`${t(\'nav.orgSwitcher\')}: ${currentName}`"')
    expect(languageSwitcher).toMatch(/\.language-switcher--navigation\.language-switcher--static\s*{[^}]*box-sizing:\s*border-box;[^}]*height:\s*32px;[^}]*line-height:\s*1\.5;/)
    expect(userMenu).toMatch(/\.nav-user-trigger__label\s*{[^}]*font-size:\s*14px;[^}]*line-height:\s*1\.5;/)
  })

  it('keeps time zone and language available in the Admin Console header', () => {
    const returnIndex = platformOpsShell.indexOf('class="platform-ops-return"')
    const timezoneIndex = platformOpsShell.indexOf('class="platform-ops-timezone platform-ops-header__desktop-utility"')
    const languageIndex = platformOpsShell.indexOf('<LanguageSwitcher variant="navigation" />')
    const userIndex = platformOpsShell.indexOf('<NavUserMenu platform />')

    expect(returnIndex).toBeGreaterThan(-1)
    expect(timezoneIndex).toBeGreaterThan(returnIndex)
    expect(languageIndex).toBeGreaterThan(timezoneIndex)
    expect(userIndex).toBeGreaterThan(languageIndex)
    expect(platformOpsShell).toContain('<Clock3')
    expect(platformOpsShell).not.toContain('class="platform-ops-timezone__label"')
    expect(platformOpsShell).toContain(':timezone-offset-display="timezoneOffsetDisplay"')
  })

  it('compacts only the interactive navigation language switcher below 1440px', () => {
    expect(languageSwitcher).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.language-switcher--navigation \.language-switcher__trigger\s*\{[\s\S]*?width:\s*32px;[\s\S]*?justify-content:\s*center;[\s\S]*?padding:\s*0;/,
    )
    expect(languageSwitcher).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.language-switcher__current,[\s\S]*?\.language-switcher--navigation \.language-switcher__trigger \.language-switcher__chevron\s*\{[\s\S]*?display:\s*none;/,
    )
  })

  it('uses consistent hover and focus feedback for compact utility icons', () => {
    expect(topNav).toContain('<div class="alerts-btn">')
    expect(topNav).not.toContain('<div class="icon-btn alerts-btn">')
    expect(topNav).toMatch(
      /@media \(min-width: 1024px\)[\s\S]*?\.right-menu\s*{[\s\S]*?gap:\s*6px/,
    )
    expect(topNav).toMatch(
      /@media \(min-width: 1024px\)[\s\S]*?\.alerts-btn\s*{[\s\S]*?margin-right:\s*-4px;[\s\S]*?margin-left:\s*-4px/,
    )
    expect(languageSwitcher).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.language-switcher--navigation \.language-switcher__trigger:hover,[\s\S]*?background:\s*var\(--icon-btn-hover-bg,[\s\S]*?color:\s*var\(--icon-btn-hover-color/,
    )
    expect(languageSwitcher).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.language-switcher--navigation \.language-switcher__globe\s*\{[\s\S]*?width:\s*18px;[\s\S]*?height:\s*18px;[\s\S]*?opacity:\s*1;/,
    )
  })

  it('keeps hidden desktop utilities reachable from mobile surfaces', () => {
    expect(drawer).toContain('target="_blank"')
    expect(drawer).toContain("$t('nav.platformOps')")
    expect(drawer).toContain('<LanguageSwitcher')
    expect(drawer).toContain("$t('nav.timezoneLabel')")
    expect(drawer).toContain('{{ timezoneOffsetDisplay }}')
    expect(userMenu).not.toContain('nav-user-timezone')
    expect(locale).toContain("timezoneLabel: 'Time Zone'")
    expect(locale).toContain("languageLabel: 'Language'")
  })

  it('mounts switching only for Enterprise tenant sites and never restores organization creation', () => {
    expect(appShell).toContain("profile?.edition === 'enterprise' && profile.site_role === 'tenant'")
    expect(topNav).toContain('v-if="showOrganizationSwitcher"')
    expect(drawer.replace(/\s+/g, ' ')).toContain('<OrgSwitcher v-if="showOrganizationSwitcher" variant="mobile" />')
    for (const source of [topNav, drawer, appShell]) {
      expect(source).not.toContain('/account/organizations/new')
    }
    expect(platformOpsShell).not.toContain('OrgSwitcher')
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
