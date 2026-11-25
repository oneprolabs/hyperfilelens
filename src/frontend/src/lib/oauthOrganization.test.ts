// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { restoreOAuthOrganization } from './oauthOrganization'
import { readLoginOrganizationPreference, rememberLoginOrganization } from './loginOrganizationPreference'

const mocks = vi.hoisted(() => ({ api: vi.fn(), fetchUser: vi.fn(), setOrg: vi.fn() }))
vi.mock('./api', () => ({ api: mocks.api }))
vi.mock('../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref(null), fetchCurrentUser: mocks.fetchUser, setStoredOrgKey: mocks.setOrg }
})
import { currentUser, type User } from '../composables/useAuth'

function user(id = 1, org = 'default', requiresPassword = false): User {
  return {
    id, email: 'user@example.test', username: 'user', requires_password_change: requiresPassword,
    access_profile: {
      org_key: org, role: 'member', visible_features: [], available_platforms: [], landing_path: '/',
    },
  }
}
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  mocks.fetchUser.mockImplementation(async () => {
    currentUser.value = user()
    return currentUser.value
  })
  mocks.api.mockImplementation(async (path: string) => path.endsWith('my-organizations/')
    ? [{ key: 'personal' }, { key: 'default' }]
    : { access_profile: { org_key: 'personal' } })
})
afterEach(() => { currentUser.value = null; localStorage.clear(); sessionStorage.clear() })

it('restores the authenticated account preference after server membership and switch verification', async () => {
  rememberLoginOrganization(1, 'personal')
  rememberLoginOrganization(2, 'another-account')
  mocks.fetchUser.mockImplementationOnce(async () => (currentUser.value = user()))
    .mockImplementationOnce(async () => (currentUser.value = user(1, 'personal')))
  await expect(restoreOAuthOrganization()).resolves.toMatchObject({ access_profile: { org_key: 'personal' } })
  expect(mocks.setOrg.mock.calls).toEqual([[''], ['personal']])
  expect(mocks.api).toHaveBeenCalledWith('/api/v1/iam/switch-organization/', {
    method: 'POST', body: JSON.stringify({ org_key: 'personal' }),
  })
  expect(mocks.fetchUser).toHaveBeenNthCalledWith(1, { rememberOrganization: false })
  expect(readLoginOrganizationPreference(1)).toBe('personal')
  expect(readLoginOrganizationPreference(2)).toBe('another-account')
})

it('ignores other accounts and legacy shared browser context when no preference exists', async () => {
  rememberLoginOrganization(2, 'personal')
  localStorage.setItem('hfl_org_key', 'personal')
  await expect(restoreOAuthOrganization()).resolves.toMatchObject({ access_profile: { org_key: 'default' } })
  expect(mocks.api).not.toHaveBeenCalled()
  expect(readLoginOrganizationPreference(1)).toBe('default')
})

it('uses the server default, not directory display order, after a preference is revoked', async () => {
  rememberLoginOrganization(1, 'revoked')
  await expect(restoreOAuthOrganization()).resolves.toMatchObject({ access_profile: { org_key: 'default' } })
  expect(mocks.api).toHaveBeenCalledTimes(1)
  expect(readLoginOrganizationPreference(1)).toBe('default')
})

it('does not overwrite a preference when directory lookup or switch confirmation fails', async () => {
  rememberLoginOrganization(1, 'personal')
  mocks.api.mockRejectedValueOnce(new Error('offline'))
  await expect(restoreOAuthOrganization()).rejects.toThrow('offline')
  expect(readLoginOrganizationPreference(1)).toBe('personal')
  expect(mocks.setOrg.mock.calls).toEqual([['']])
})

it('does not select a different organization when the server refuses it', async () => {
  rememberLoginOrganization(1, 'personal')
  mocks.api.mockResolvedValueOnce([{ key: 'personal' }, { key: 'default' }])
    .mockResolvedValueOnce({ access_profile: { org_key: 'default' } })
  await expect(restoreOAuthOrganization()).rejects.toThrow('confirmation changed')
  expect(mocks.setOrg.mock.calls).toEqual([['']])
  expect(readLoginOrganizationPreference(1)).toBe('personal')
})

it('rejects account changes during organization confirmation', async () => {
  rememberLoginOrganization(1, 'personal')
  mocks.api.mockResolvedValueOnce([{ key: 'personal' }, { key: 'default' }])
    .mockImplementationOnce(async () => {
      currentUser.value = user(2)
      return { access_profile: { org_key: 'personal' } }
    })
  await expect(restoreOAuthOrganization()).rejects.toThrow('confirmation changed')
  expect(mocks.setOrg.mock.calls).toEqual([['']])
})

it('rejects account changes while checking a revoked preference', async () => {
  rememberLoginOrganization(1, 'revoked')
  mocks.api.mockImplementationOnce(async () => {
    currentUser.value = user(2)
    return [{ key: 'default' }]
  })
  await expect(restoreOAuthOrganization()).rejects.toThrow('account changed')
  expect(readLoginOrganizationPreference(1)).toBe('revoked')
})

it.each([
  ['another account', user(2, 'personal')],
  ['another organization', user(1, 'default')],
  ['an unavailable session', null],
] as const)('does not remember an unverified target after switching to %s', async (_label, result) => {
  rememberLoginOrganization(1, 'personal')
  mocks.fetchUser.mockImplementationOnce(async () => (currentUser.value = user()))
    .mockImplementationOnce(async () => (currentUser.value = result))
  await expect(restoreOAuthOrganization()).rejects.toThrow('no longer available')
  expect(readLoginOrganizationPreference(1)).toBe('personal')
  expect(readLoginOrganizationPreference(2)).toBe('')
})

it('does not consult preferences or memberships without an authenticated account', async () => {
  rememberLoginOrganization(1, 'personal')
  mocks.fetchUser.mockResolvedValueOnce(null)
  await expect(restoreOAuthOrganization()).rejects.toThrow('session is unavailable')
  expect(mocks.api).not.toHaveBeenCalled()
  expect(readLoginOrganizationPreference(1)).toBe('personal')
})

it('does not call setup-restricted organization endpoints before mandatory password setup', async () => {
  rememberLoginOrganization(1, 'personal')
  mocks.fetchUser.mockImplementationOnce(async () => (currentUser.value = user(1, 'default', true)))
  await expect(restoreOAuthOrganization()).resolves.toMatchObject({ requires_password_change: true })
  expect(mocks.api).not.toHaveBeenCalled()
  expect(readLoginOrganizationPreference(1)).toBe('personal')
})
