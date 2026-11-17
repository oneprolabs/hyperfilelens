// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { completeOrganizationSwitch } from './organizationSwitch'
import { readStoredOrganizationKey } from './organizationStorage'
import { getRouteRequestSignal } from './routeRequestAbort'

const navigate = vi.fn()
const location = { origin: 'https://tenant.example.test', assign: navigate }
beforeEach(() => {
  navigate.mockReset()
  localStorage.setItem('hfl_org_key', 'org-a')
  sessionStorage.setItem('hfl_org_key', 'org-a')
  vi.stubGlobal('window', { location })
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
})

it('hands the verified organization to the new document without changing old context', () => {
  const signal = getRouteRequestSignal()
  completeOrganizationSwitch('org-b', '/')
  expect(navigate).toHaveBeenCalledWith('/?org=org-b')
  // assign() returns normally even when beforeunload cancels navigation.
  expect(readStoredOrganizationKey()).toBe('org-a')
  expect(localStorage.getItem('hfl_org_key')).toBe('org-a')
  expect(signal.aborted).toBe(false)
  expect(sessionStorage.getItem('hfl_org_switch_probe')).toBeNull()
})

it('preserves the old context if navigation throws', () => {
  navigate.mockImplementation(() => { throw new Error('Unavailable') })
  expect(() => completeOrganizationSwitch('org-b', '/')).toThrow('Unavailable')
  expect(readStoredOrganizationKey()).toBe('org-a')
  expect(localStorage.getItem('hfl_org_key')).toBe('org-a')
})

it('pins the old context when this tab previously used the shared default', () => {
  sessionStorage.removeItem('hfl_org_key')
  completeOrganizationSwitch('org-b', '/')
  localStorage.setItem('hfl_org_key', 'another-tab')
  expect(readStoredOrganizationKey()).toBe('org-a')
})

it.each(['getItem', 'setItem'] as const)('refuses navigation when sessionStorage.%s throws', (method) => {
  const storage = {
    getItem: sessionStorage.getItem.bind(sessionStorage),
    setItem: sessionStorage.setItem.bind(sessionStorage),
    removeItem: sessionStorage.removeItem.bind(sessionStorage),
    [method]: () => { throw new Error('Storage denied') },
  }
  vi.stubGlobal('sessionStorage', storage)
  expect(() => completeOrganizationSwitch('org-b', '/')).toThrow()
  expect(navigate).not.toHaveBeenCalled()
  expect(localStorage.getItem('hfl_org_key')).toBe('org-a')
})

it('refuses navigation when tab storage silently drops writes', () => {
  vi.stubGlobal('sessionStorage', {
    getItem: sessionStorage.getItem.bind(sessionStorage),
    setItem: () => {},
    removeItem: sessionStorage.removeItem.bind(sessionStorage),
  })
  expect(() => completeOrganizationSwitch('org-b', '/')).toThrow('Tab organization storage')
  expect(navigate).not.toHaveBeenCalled()
  expect(readStoredOrganizationKey()).toBe('org-a')
})

it('does not require shared localStorage to switch with a valid pinned tab context', () => {
  vi.stubGlobal('localStorage', {
    getItem: () => { throw new Error('Storage denied') },
  })
  completeOrganizationSwitch('org-b', '/')
  expect(navigate).toHaveBeenCalledWith('/?org=org-b')
  expect(sessionStorage.getItem('hfl_org_key')).toBe('org-a')
})

it('preserves landing parameters and replaces an existing organization selector', () => {
  completeOrganizationSwitch('org-b', '/ops/tasks?tab=recent&org=old#tasks')
  expect(navigate).toHaveBeenCalledWith('/ops/tasks?tab=recent&org=org-b#tasks')
})

it.each(['//foreign.example/', '/\\foreign.example/', '/platform-ops/users', '/admin/', '/login'])(
  'refuses an unsafe or non-tenant destination: %s', (path) => {
    expect(() => completeOrganizationSwitch('org-b', path)).toThrow('Invalid organization switch')
    expect(navigate).not.toHaveBeenCalled()
    expect(readStoredOrganizationKey()).toBe('org-a')
  },
)
