// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { installOrganizationHistoryGuard } from './organizationHistory'
import {
  isOrganizationContextFrozen, prepareTabOrganizationContext, readStoredOrganizationKey,
} from './organizationStorage'
import { asyncNoticeScope } from './asyncNoticeLedger'
import {
  markWizardPendingBySourceIds, readWizardPendingSourceOps,
} from '../pages/protection/composables/backupWizardPendingStorage'

let dispose: () => void
let events: EventTarget
const reload = vi.fn()

beforeEach(() => {
  reload.mockReset()
  events = new EventTarget()
  vi.stubGlobal('window', Object.assign(events, { location: { reload } }))
  localStorage.setItem('hfl_org_key', 'org-a')
  sessionStorage.setItem('hfl_org_key', 'org-a')
  dispose = installOrganizationHistoryGuard()
})
afterEach(() => {
  dispose()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
})

it('does not freeze or reload the normal initial page show', () => {
  events.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false }))
  expect(reload).not.toHaveBeenCalled()
  expect(isOrganizationContextFrozen()).toBe(false)
})

it('does not freeze a cancelled beforeunload or a non-cached page departure', () => {
  events.dispatchEvent(new Event('beforeunload', { cancelable: true }))
  events.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }))
  expect(isOrganizationContextFrozen()).toBe(false)
  expect(reload).not.toHaveBeenCalled()
})

it('keeps a restored document pinned until fresh bootstrap, including resumed async work', () => {
  markWizardPendingBySourceIds(['agent:9'], { kind: 'deleting', taskUuid: 'org-a-task' })
  events.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))
  // A later document in this same tab has switched to B.
  localStorage.setItem('hfl_org_key', 'org-b')
  sessionStorage.setItem('hfl_org_key', 'org-b')
  events.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
  expect(reload).toHaveBeenCalledOnce()
  expect(readStoredOrganizationKey()).toBe('org-a')
  expect(asyncNoticeScope()).toBe('org-a')
  expect(readWizardPendingSourceOps().get('agent:9')?.taskUuid).toBe('org-a-task')
  expect(() => prepareTabOrganizationContext()).toThrow('document reload')
  expect(sessionStorage.getItem('hfl_org_key')).toBe('org-b')
})

it('reloads a persisted page even when the organization has not changed', () => {
  events.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))
  events.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
  events.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
  expect(reload).toHaveBeenCalledOnce()
  expect(isOrganizationContextFrozen()).toBe(true)
})

it('also keeps an empty cached context from adopting a later login context', () => {
  sessionStorage.removeItem('hfl_org_key')
  localStorage.removeItem('hfl_org_key')
  events.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))
  sessionStorage.setItem('hfl_org_key', 'org-b')
  events.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
  expect(readStoredOrganizationKey()).toBe('')
  expect(isOrganizationContextFrozen()).toBe(true)
  expect(reload).toHaveBeenCalledOnce()
})

it('removes event listeners and releases document scope on disposal', () => {
  events.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))
  dispose()
  sessionStorage.setItem('hfl_org_key', 'org-b')
  events.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
  expect(reload).not.toHaveBeenCalled()
  expect(isOrganizationContextFrozen()).toBe(false)
  expect(readStoredOrganizationKey()).toBe('org-b')
})

it('installs before asynchronous bootstrap and session watchers can run', () => {
  const source = readFileSync(resolve('src/main.ts'), 'utf8')
  const installation = source.indexOf('  installOrganizationHistoryGuard()')
  expect(installation).toBeGreaterThan(0)
  expect(installation).toBeLessThan(source.indexOf('  await initSentry('))
  expect(installation).toBeLessThan(source.indexOf('  setupSessionWatchdog()'))
})
