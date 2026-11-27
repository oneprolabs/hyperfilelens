// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { impersonationContext, markImpersonationEnded, returnToAdmin, saveImpersonation } from './impersonation'

const id = '9ad09efb-cf40-4d00-8343-56a60eeeb329'
const adminUrl = 'https://admin.example.test/platform-ops/users'
const assign = vi.fn()

beforeEach(() => {
  assign.mockClear()
  impersonationContext.value = null
  sessionStorage.clear()
})

afterEach(() => {
  impersonationContext.value = null
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

function location(pathname: string) {
  vi.stubGlobal('location', { pathname, assign })
}

describe('explicit return from inert impersonation pages', () => {
  it('returns from a failed entry with no established scope', () => {
    location('/impersonation/entry')
    returnToAdmin(adminUrl)
    expect(assign).toHaveBeenCalledExactlyOnceWith(adminUrl)
  })

  it('retains a live entry scope until it is marked ended', () => {
    location('/impersonation/entry')
    saveImpersonation({ id, org_key: 'personal' })
    returnToAdmin(adminUrl)
    expect(assign).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('hfl_impersonation_context')).toContain(id)
    markImpersonationEnded()
    returnToAdmin(adminUrl)
    expect(assign).toHaveBeenCalledExactlyOnceWith(adminUrl)
    expect(sessionStorage.getItem('hfl_impersonation_context')).toBeNull()
    // The old document stays fenced even if full navigation has not completed.
    expect(impersonationContext.value).toMatchObject({ id, ended: true })
  })

  it('preserves the existing full-navigation exit on the ended page', () => {
    location('/impersonation/ended')
    saveImpersonation({ id, ended: true })
    returnToAdmin(adminUrl)
    expect(assign).toHaveBeenCalledExactlyOnceWith(adminUrl)
    expect(sessionStorage.getItem('hfl_impersonation_context')).toBeNull()
  })

  it.each(['/overview', '/platform-ops/users', '/protection/backups'])(
    'never discards the selector on an application page: %s', path => {
      location(path)
      saveImpersonation({ id, ended: true })
      returnToAdmin(adminUrl)
      expect(assign).not.toHaveBeenCalled()
      expect(sessionStorage.getItem('hfl_impersonation_context')).toContain(id)
    },
  )

  it.each(['not a URL', '/platform-ops', 'javascript:alert(1)', 'data:text/html,admin', 'https://user:password@admin.example.test'])(
    'rejects unsafe return URLs without discarding the fence: %s', url => {
      location('/impersonation/ended')
      saveImpersonation({ id, ended: true })
      returnToAdmin(url)
      expect(assign).not.toHaveBeenCalled()
      expect(sessionStorage.getItem('hfl_impersonation_context')).toContain(id)
    },
  )
})
