// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

function route(path: string, ...recordPaths: string[]): RouteLocationNormalizedLoaded {
  return {
    path,
    fullPath: path,
    matched: (recordPaths.length ? recordPaths : [path]).map((recordPath) => ({
      path: recordPath,
    })),
  } as RouteLocationNormalizedLoaded
}

function dataLayerCommands(): unknown[][] {
  return (window.dataLayer || []).map((command) => (
    Array.from(command as ArrayLike<unknown>)
  ))
}

async function loadAnalytics(measurementId = '') {
  vi.resetModules()
  window.__HFL_APP_CONFIG__ = { gaMeasurementId: measurementId }
  const afterEach = vi.fn()
  const analytics = await import('./analytics')
  analytics.initAppAnalytics({ afterEach } as unknown as Router)
  return { analytics, afterEach }
}

afterEach(() => {
  document.head.querySelectorAll('script[data-hfl-google-analytics]').forEach((node) => node.remove())
  delete window.__HFL_APP_CONFIG__
  delete window.dataLayer
  delete window.gtag
  vi.restoreAllMocks()
})

describe('runtime Google Analytics', () => {
  it('does not load Google when the SaaS runtime variable is absent', async () => {
    const { afterEach } = await loadAnalytics()

    expect(afterEach).not.toHaveBeenCalled()
    expect(document.querySelector('script[data-hfl-google-analytics]')).toBeNull()
    expect(window.gtag).toBeUndefined()
  })

  it('warns and remains disabled for an invalid measurement ID', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const { afterEach } = await loadAnalytics('not-a-measurement-id')

    expect(warn).toHaveBeenCalledWith(
      '[analytics] Invalid GA4 measurement ID; analytics is disabled.',
    )
    expect(afterEach).not.toHaveBeenCalled()
    expect(document.querySelector('script[data-hfl-google-analytics]')).toBeNull()
  })

  it('loads gtag and reports only the route template without query or resource IDs', async () => {
    const { analytics, afterEach } = await loadAnalytics('G-0RX9GZJCWF')
    const hook = afterEach.mock.calls[0]?.[0] as (to: RouteLocationNormalizedLoaded) => void

    expect(document.querySelector<HTMLScriptElement>('script[data-hfl-google-analytics]')?.src)
      .toBe('https://www.googletagmanager.com/gtag/js?id=G-0RX9GZJCWF')
    hook(route(
      '/protection/backups/customer-backup?token=secret',
      '/',
      'protection/backups/:backupId',
    ))

    expect(window.dataLayer?.every((command) => (
      Object.prototype.toString.call(command) === '[object Arguments]'
    ))).toBe(true)
    expect(dataLayerCommands()).toEqual(expect.arrayContaining([
      ['config', 'G-0RX9GZJCWF', { send_page_view: false }],
      ['event', 'page_view', expect.objectContaining({
        page_location: `${window.location.origin}/protection/backups/:backupId`,
        page_path: '/protection/backups/:backupId',
        page_title: 'HyperFileLens',
      })],
    ]))
    expect(JSON.stringify(window.dataLayer)).not.toContain('customer-backup')
    expect(JSON.stringify(window.dataLayer)).not.toContain('secret')

    window.history.replaceState({}, '', '/register?email=person@example.com')
    analytics.trackAppEvent('sign_up', { method: 'email' })
    expect(dataLayerCommands()).toContainEqual([
      'event',
      'sign_up',
      expect.objectContaining({
        method: 'email',
        page_location: `${window.location.origin}/protection/backups/:backupId`,
        page_path: '/protection/backups/:backupId',
      }),
    ])
    expect(JSON.stringify(dataLayerCommands())).not.toContain('person@example.com')
  })
})
