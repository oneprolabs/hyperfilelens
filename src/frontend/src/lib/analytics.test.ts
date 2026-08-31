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

async function loadAnalytics(
  measurementId = '',
  surface: 'tenant' | 'admin' = 'tenant',
  activate = true,
) {
  vi.resetModules()
  window.__HFL_APP_CONFIG__ = { gaMeasurementId: measurementId, sentrySurface: surface }
  const afterEach = vi.fn()
  const analytics = await import('./analytics')
  analytics.initAppAnalytics({ afterEach } as unknown as Router)
  if (activate) analytics.activateAppAnalytics()
  return { analytics, afterEach }
}

afterEach(() => {
  document.head.querySelectorAll('script[data-hfl-google-analytics]').forEach((node) => node.remove())
  delete window.__HFL_APP_CONFIG__
  delete window.dataLayer
  delete window.gtag
  document.title = 'HyperFileLens'
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
        page_title: 'Backup Details | HyperFileLens Console',
        page_key: 'protection.backup_detail',
        page_group: 'protection',
        page_surface: 'console',
        ui_language: 'en',
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
        page_key: 'protection.backup_detail',
      }),
    ])
    expect(JSON.stringify(dataLayerCommands())).not.toContain('person@example.com')
  })

  it('normalizes controlled sidebar query values to business pages', async () => {
    const { analytics } = await loadAnalytics('G-0RX9GZJCWF')
    expect(analytics.analyticsPageMetadata({
      ...route('/protection/backup-sources?tab=host', '/protection/backup-sources'),
      query: { tab: 'host' },
    } as RouteLocationNormalizedLoaded))
      .toMatchObject({
        pageKey: 'protection.source_hosts',
        pagePath: '/protection/source-hosts',
      })
    expect(analytics.analyticsPageMetadata({
      ...route('/protection/backup-sources?tab=nas', '/protection/backup-sources'),
      query: { tab: 'nas' },
    } as RouteLocationNormalizedLoaded)).toMatchObject({
      pageKey: 'protection.source_nas',
      pagePath: '/protection/source-nas',
    })
    expect(analytics.analyticsPageMetadata(
      route('/node/repositories', '/node/repositories'),
    )).toMatchObject({
      pageKey: 'protection.object_storage',
      pagePath: '/protection/object-storage',
    })
    expect(analytics.analyticsPageMetadata({
      ...route('/protection/policies?tab=unknown', '/protection/policies'),
      query: { tab: 'unknown' },
    } as RouteLocationNormalizedLoaded)).toMatchObject({
      pageKey: 'protection.backup_policies',
      pagePath: '/protection/backup-policies',
    })
    expect(analytics.analyticsPageMetadata(
      route('/node/snapshots', '/node/snapshots'),
    )).toMatchObject({
      pageKey: 'protection.snapshots',
      pageGroup: 'protection',
      pagePath: '/node/snapshots',
    })
  })

  it('keeps secondary pages in their top-level product groups', async () => {
    const { analytics } = await loadAnalytics('G-0RX9GZJCWF')
    expect(analytics.analyticsPageMetadata(route(
      '/insight/copilot/new',
      '/',
      'insight/copilot/new',
    ))).toMatchObject({
      pageKey: 'insight.ai_copilot',
      pageGroup: 'insights',
    })
    expect(analytics.analyticsPageMetadata(route(
      '/ops/alerts/rules/customer-rule/edit',
      '/',
      'ops/alerts/rules/:id/edit',
    ))).toMatchObject({
      pageKey: 'operations.alert_rules',
      pageGroup: 'operations',
      pagePath: '/ops/alerts/rules/:id/edit',
    })
  })

  it('does not report Admin Console pages unless explicitly enabled', async () => {
    const { afterEach } = await loadAnalytics('G-0RX9GZJCWF', 'admin')
    expect(afterEach).not.toHaveBeenCalled()
    expect(document.querySelector('script[data-hfl-google-analytics]')).toBeNull()
  })

  it('consumes extension-declared Admin metadata without exposing resource IDs', async () => {
    const { analytics, afterEach } = await loadAnalytics('G-0RX9GZJCWF')
    const declaredRoute = {
      ...route('/platform-ops/orgs/secret-org', '/platform-ops', 'orgs/:id'),
      matched: [
        { path: '/platform-ops' },
        {
          path: 'orgs/:id',
          meta: {
            analytics: {
              pageKey: 'platform.organization_detail',
              pageGroup: 'platform',
              pageSurface: 'admin',
              titleKey: 'platformOps.orgs.detailTitle',
            },
          },
        },
      ],
    } as RouteLocationNormalizedLoaded
    expect(analytics.analyticsPageMetadata(declaredRoute)).toMatchObject({
      pageKey: 'platform.organization_detail',
      pageSurface: 'admin',
      pagePath: '/platform-ops/orgs/:id',
    })
    const hook = afterEach.mock.calls[0]?.[0] as (to: RouteLocationNormalizedLoaded) => void
    hook(declaredRoute)
    expect(JSON.stringify(window.dataLayer)).not.toContain('secret-org')
  })

  it('does not duplicate a page view and never exposes an unmatched path', async () => {
    const { analytics, afterEach } = await loadAnalytics('G-0RX9GZJCWF')
    const hook = afterEach.mock.calls[0]?.[0] as (to: RouteLocationNormalizedLoaded) => void
    const overview = route('/', '/')
    hook(overview)
    hook(overview)
    expect(dataLayerCommands().filter((command) => (
      command[0] === 'event' && command[1] === 'page_view'
    ))).toHaveLength(1)

    const unknown = analytics.analyticsPageMetadata(route('/private/customer-resource'))
    expect(unknown).toMatchObject({ pageKey: 'route.other', pagePath: '/other' })
    expect(JSON.stringify(unknown)).not.toContain('customer-resource')

    const unknownAdmin = analytics.analyticsPageMetadata(route(
      '/platform-ops/orgs/secret-org',
      '/platform-ops',
      'orgs/:id/unknown',
    ))
    expect(unknownAdmin).toMatchObject({
      pageKey: 'route.other',
      pageSurface: 'admin',
      pagePath: '/other',
    })
    expect(JSON.stringify(unknownAdmin)).not.toContain('secret-org')
  })

  it('holds the first page view until locale startup is complete', async () => {
    const { analytics, afterEach } = await loadAnalytics('G-0RX9GZJCWF', 'tenant', false)
    const hook = afterEach.mock.calls[0]?.[0] as (to: RouteLocationNormalizedLoaded) => void
    hook(route('/', '/'))
    expect(dataLayerCommands().some((command) => command[1] === 'page_view')).toBe(false)
    analytics.activateAppAnalytics()
    expect(dataLayerCommands().filter((command) => command[1] === 'page_view')).toHaveLength(1)
  })
})
