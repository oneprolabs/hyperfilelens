// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import {
  LANG_STORAGE_KEY,
  LOGIN_LOCALE_STORAGE_KEY,
} from '../i18n'
import { clearLogoutBrowserStorage } from './logoutStorage'
import { readLoginOrganizationPreference, rememberLoginOrganization } from './loginOrganizationPreference'

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('clearLogoutBrowserStorage', () => {
  it('preserves only the anonymous language preference', () => {
    localStorage.setItem(LANG_STORAGE_KEY, 'zh-hans')
    localStorage.setItem('hfl_org_key', 'org-1')
    localStorage.setItem('sidebar-collapsed', 'true')
    localStorage.setItem('theme', 'dark')
    sessionStorage.setItem(LOGIN_LOCALE_STORAGE_KEY, 'en')

    clearLogoutBrowserStorage()

    expect(localStorage).toHaveLength(1)
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe('zh-hans')
    expect(sessionStorage.getItem(LOGIN_LOCALE_STORAGE_KEY)).toBeNull()
  })

  it('does not create a language preference when none existed', () => {
    localStorage.setItem('hfl_org_key', 'org-1')

    clearLogoutBrowserStorage()

    expect(localStorage).toHaveLength(0)
  })

  it('retains account preferences but removes identity, active organization and malformed keys', () => {
    rememberLoginOrganization(1, 'personal')
    rememberLoginOrganization(2, 'enterprise')
    localStorage.setItem('hfl_org_key', 'personal')
    sessionStorage.setItem('hfl_org_key', 'personal')
    localStorage.setItem('access_token', 'must-clear')
    localStorage.setItem('hfl_login_org:token', 'must-clear')
    clearLogoutBrowserStorage()
    expect(readLoginOrganizationPreference(1)).toBe('personal')
    expect(readLoginOrganizationPreference(2)).toBe('enterprise')
    expect(localStorage.getItem('hfl_org_key')).toBeNull()
    expect(sessionStorage.getItem('hfl_org_key')).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('hfl_login_org:token')).toBeNull()
    expect(localStorage.length).toBe(2)
  })
})
