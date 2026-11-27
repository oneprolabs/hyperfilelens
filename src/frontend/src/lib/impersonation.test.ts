// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { impersonationContext, saveImpersonation, impersonationHeaders, scopedImpersonationUrl, markImpersonationEnded, installImpersonationTransport } from './impersonation'
import { readStoredOrganizationKey } from './organizationStorage'

const id = '9ad09efb-cf40-4d00-8343-56a60eeeb329'
afterEach(() => {
  impersonationContext.value = null
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
describe('tab-scoped impersonation', () => {
  it('uses only tab storage and does not overwrite either account login preference', () => {
    localStorage.setItem('hfl_org_key', 'admin-org')
    localStorage.setItem('hfl_login_org:10', 'admin-org')
    localStorage.setItem('hfl_login_org:20', 'customer-last-org')
    saveImpersonation({ id, org_key: 'customer-org', user_id: 20, actor_id: 10 })
    expect(readStoredOrganizationKey()).toBe('customer-org')
    expect(impersonationHeaders()).toEqual({ 'X-HFL-Impersonation': id, 'X-Org-Key': 'customer-org' })
    expect(localStorage.getItem('hfl_org_key')).toBe('admin-org')
    expect(localStorage.getItem('hfl_login_org:20')).toBe('customer-last-org')
    expect(localStorage.getItem('hfl_login_org:10')).toBe('admin-org')
    expect(sessionStorage.getItem('hfl_impersonation_context')).toContain(id)
  })
  it('retains a fail-closed marker after ending rather than reverting to the actor', () => {
    saveImpersonation({ id, org_key: 'customer-org' })
    markImpersonationEnded()
    expect(impersonationContext.value?.ended).toBe(true)
    expect(impersonationHeaders()['X-HFL-Impersonation']).toBe(id)
    expect(readStoredOrganizationKey()).toBe('customer-org')
  })
  it('refuses to enter when tab storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => saveImpersonation({ id })).toThrow('blocked')
    expect(impersonationContext.value).toBeNull()
  })
  it('carries only an opaque selector into same-origin links and never into external links', () => {
    saveImpersonation({ id })
    expect(new URL(scopedImpersonationUrl('/api/v1/file/download')).searchParams.get('impersonation')).toBe(id)
    expect(scopedImpersonationUrl('https://other.test/download')).toBe('https://other.test/download')
    const blob = `blob:${window.location.origin}/file-id`
    expect(scopedImpersonationUrl(blob)).toBe(blob)
    expect(new URL(scopedImpersonationUrl('/protection/backups')).searchParams.get('impersonation')).toBe(id)
  })
  it('covers direct streaming fetch and downloads without sending context to another origin', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    vi.spyOn(window, 'open').mockReturnValue(null)
    saveImpersonation({ id, org_key: 'customer-org' })
    installImpersonationTransport()
    await window.fetch('/api/v1/lens/chats/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    const headers = fetch.mock.calls[0][1].headers as Headers
    expect(headers.get('X-HFL-Impersonation')).toBe(id)
    expect(headers.get('X-Org-Key')).toBe('customer-org')
    expect(headers.get('Content-Type')).toBe('application/json')
    await window.fetch('https://outside.test/api/v1/file')
    expect(fetch.mock.calls[1]).toEqual(['https://outside.test/api/v1/file', undefined])
    const link = document.createElement('a')
    link.href = '/protection/backups'
    document.body.appendChild(link)
    link.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true }))
    expect(new URL(link.href).searchParams.get('impersonation')).toBe(id)
    link.remove()
    markImpersonationEnded()
    await expect(window.fetch('/api/v1/auth/logout', { method: 'POST' })).rejects.toThrow('session has ended')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
