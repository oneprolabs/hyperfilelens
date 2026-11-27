// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  refreshAuthToken: vi.fn(),
}))

vi.mock('../router', () => ({
  router: {
    currentRoute: { value: { path: '/login', fullPath: '/login' } },
    beforeEach: vi.fn(),
    replace: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('../lib/authRefresh', () => ({
  refreshAuthToken: mocks.refreshAuthToken,
}))

vi.mock('../lib/requestContext', () => ({
  getCorrelationHeaders: () => ({}),
}))

vi.mock('./useDeployProfile', () => ({
  clearDeployProfileCache: vi.fn(),
  fetchDeployProfile: vi.fn(),
  resolvePostLoginPath: vi.fn().mockResolvedValue('/'),
  shouldForceDeployProfileRefresh: vi.fn().mockReturnValue(false),
}))

import { confirmCurrentSession, currentUser, fetchCurrentUser } from './useAuth'
import { readLoginOrganizationPreference, rememberLoginOrganization } from '../lib/loginOrganizationPreference'
import { impersonationContext, saveImpersonation } from '../lib/impersonation'
import { freezeOrganizationContext, releaseOrganizationContext } from '../lib/organizationStorage'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('confirmCurrentSession', () => {
  beforeEach(() => {
    mocks.refreshAuthToken.mockReset()
    currentUser.value = null
    impersonationContext.value = null
    localStorage.clear()
    sessionStorage.clear()
    releaseOrganizationContext()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    currentUser.value = null
    impersonationContext.value = null
    releaseOrganizationContext()
    localStorage.clear()
    sessionStorage.clear()
  })

  it('returns the authenticated user when the session can be inspected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      data: {
        user: { id: 7, email: 'person@example.com', username: 'person' },
        refresh_available: false,
      },
    })))

    await expect(confirmCurrentSession()).resolves.toMatchObject({
      state: 'authenticated',
      user: { id: 7 },
    })
  })

  it('shares one in-flight session check across startup consumers', async () => {
    let resolveFetch!: (response: Response) => void
    const response = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })
    const fetchMock = vi.fn().mockReturnValue(response)
    vi.stubGlobal('fetch', fetchMock)

    const first = confirmCurrentSession()
    const second = confirmCurrentSession()
    resolveFetch(jsonResponse({
      data: {
        user: { id: 9, email: 'shared@example.com', username: 'shared' },
        refresh_available: false,
      },
    }))

    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.objectContaining({ state: 'authenticated' }),
      expect.objectContaining({ state: 'authenticated' }),
    ])
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('distinguishes an explicitly absent session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 401)))

    await expect(confirmCurrentSession()).resolves.toEqual({ state: 'unauthenticated' })
  })

  it('keeps the result unknown when session inspection cannot connect', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network unavailable')))

    await expect(confirmCurrentSession()).resolves.toEqual({ state: 'unknown' })
  })

  it('keeps the result unknown when token refresh loses its response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      data: { user: null, refresh_available: true },
    })))
    mocks.refreshAuthToken.mockResolvedValue({ ok: false, networkError: true })

    await expect(confirmCurrentSession()).resolves.toEqual({ state: 'unknown' })
  })

  it('remembers only the organization confirmed in the server access profile', async () => {
    rememberLoginOrganization(8, 'another-account')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      user: { id: 7, access_profile: { org_key: 'verified-org' } },
    })))
    await fetchCurrentUser()
    expect(readLoginOrganizationPreference(7)).toBe('verified-org')
    expect(readLoginOrganizationPreference(8)).toBe('another-account')
  })

  it('does not overwrite a login preference during OAuth bootstrap', async () => {
    rememberLoginOrganization(7, 'personal')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      user: { id: 7, access_profile: { org_key: 'server-default' } },
    })))
    await fetchCurrentUser({ rememberOrganization: false })
    expect(readLoginOrganizationPreference(7)).toBe('personal')
  })

  it.each([
    { authority_source: 'support_readonly' },
    { is_organization_member: false },
  ])('does not remember support or non-member organization contexts (%j)', async (profile) => {
    rememberLoginOrganization(7, 'personal')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      user: { id: 7, access_profile: { org_key: 'support-org', ...profile } },
    })))
    await fetchCurrentUser()
    expect(readLoginOrganizationPreference(7)).toBe('personal')
  })

  it('does not let a frozen old document overwrite the next login preference', async () => {
    rememberLoginOrganization(7, 'personal')
    sessionStorage.setItem('hfl_org_key', 'old-org')
    freezeOrganizationContext()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      user: { id: 7, access_profile: { org_key: 'old-org' } },
    })))
    await fetchCurrentUser()
    expect(readLoginOrganizationPreference(7)).toBe('personal')
  })
})


it('restores only a server-confirmed impersonation without changing browser organization defaults', async () => {
  const id = '9ad09efb-cf40-4d00-8343-56a60eeeb329'
  localStorage.setItem('hfl_org_key', 'admin-org')
  saveImpersonation({ id })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ user: {
    id: 20, impersonation: { id, user_id: 20, org_key: 'customer-org', user_email: 'customer@example.test' },
    access_profile: { org_key: 'customer-org' },
  } })))
  try {
    expect((await fetchCurrentUser())?.id).toBe(20)
    expect(impersonationContext.value?.org_key).toBe('customer-org')
    expect(localStorage.getItem('hfl_org_key')).toBe('admin-org')
    expect(readLoginOrganizationPreference(20)).toBe('')
  } finally {
    impersonationContext.value = null; currentUser.value = null; localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals()
  }
})

it('never installs an actor profile into an impersonation document after confirmation fails', async () => {
  saveImpersonation({ id: '9ad09efb-cf40-4d00-8343-56a60eeeb329' })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ user: {
    id: 10, is_staff: true, access_profile: { org_key: 'admin-org' },
  } })))
  currentUser.value = null
  try {
    expect(await fetchCurrentUser()).toBeNull()
    expect(currentUser.value).toBeNull()
    expect(impersonationContext.value?.ended).toBe(true)
  } finally {
    impersonationContext.value = null; currentUser.value = null; sessionStorage.clear(); vi.unstubAllGlobals()
  }
})
