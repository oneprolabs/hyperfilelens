// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import {
  LANG_STORAGE_KEY,
  LOGIN_LOCALE_STORAGE_KEY,
} from '../i18n'
import { clearLogoutBrowserStorage } from './logoutStorage'

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
})
