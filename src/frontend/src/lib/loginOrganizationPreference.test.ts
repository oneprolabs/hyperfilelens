// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import {
  isLoginOrganizationPreferenceKey, readLoginOrganizationPreference, rememberLoginOrganization,
} from './loginOrganizationPreference'
import { chooseLoginOrganization } from './organizationStorage'

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear() })

it('keeps browser login preferences separate for each authenticated user', () => {
  rememberLoginOrganization(1, 'personal')
  rememberLoginOrganization(2, 'enterprise')
  localStorage.setItem('hfl_org_key', 'another-account')
  sessionStorage.setItem('hfl_org_key', 'another-account')
  expect(readLoginOrganizationPreference(1)).toBe('personal')
  expect(readLoginOrganizationPreference(2)).toBe('enterprise')
  expect(readLoginOrganizationPreference(3)).toBe('')
  expect(readLoginOrganizationPreference(undefined)).toBe('')
})

it('uses a remembered choice only while it remains in the server membership list', () => {
  rememberLoginOrganization(1, 'removed')
  const choices = [{ org_key: 'a' }, { org_key: 'default' }]
  expect(chooseLoginOrganization(choices, readLoginOrganizationPreference(1), 'default')).toBe('default')
  expect(chooseLoginOrganization(choices, 'a', 'default')).toBe('a')
  expect(chooseLoginOrganization(choices, 'removed', 'missing')).toBe('a')
  expect(chooseLoginOrganization([], 'removed', 'default')).toBe('')
})

it('keeps blocked browser storage optional', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  expect(readLoginOrganizationPreference(1)).toBe('')
  expect(() => rememberLoginOrganization(1, 'personal')).not.toThrow()
})

it('retains only valid account preference keys rather than arbitrary prefixed state', () => {
  expect(isLoginOrganizationPreferenceKey('hfl_login_org:12')).toBe(true)
  for (const key of ['hfl_org_key', 'hfl_login_org:', 'hfl_login_org:0', 'hfl_login_org:01', 'hfl_login_org:token']) {
    expect(isLoginOrganizationPreferenceKey(key)).toBe(false)
  }
  rememberLoginOrganization(0, 'invalid')
  rememberLoginOrganization(Number.NaN, 'invalid')
  rememberLoginOrganization(1, ' ')
  expect(localStorage.length).toBe(0)
})
