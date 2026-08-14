// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import type { App } from 'vue'
import type { Router } from 'vue-router'

const sentryInit = vi.fn()
vi.mock('@sentry/vue', () => ({
  init: sentryInit,
  browserTracingIntegration: vi.fn(() => 'browser-tracing'),
}))

afterEach(() => {
  sentryInit.mockReset()
  delete window.__HFL_APP_CONFIG__
  vi.restoreAllMocks()
})

describe('runtime Sentry', () => {
  it('remains disabled when runtime configuration is absent', async () => {
    const { initSentry } = await import('./sentry')
    const load = vi.fn(() => import('./sentrySdk'))
    await initSentry({} as App, { afterEach: vi.fn() } as unknown as Router, load)
    expect(sentryInit).not.toHaveBeenCalled()
    expect(load).not.toHaveBeenCalled()
  })

  it('warns and continues when the optional SDK chunk cannot load', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    window.__HFL_APP_CONFIG__ = {
      sentryEnabled: true,
      sentryDsn: 'https://public@sentry.example.com/42',
      sentryEnvironment: 'hfl-test',
      sentryRelease: 'hyperfilelens-frontend@main-1234567',
    }
    const { initSentry } = await import('./sentry')

    await expect(initSentry(
      {} as App,
      { afterEach: vi.fn() } as unknown as Router,
      async () => { throw new Error('chunk unavailable') },
    )).resolves.toBeUndefined()
    expect(warn).toHaveBeenCalledWith(
      '[sentry] Initialization failed; the application will continue.',
      expect.any(Error),
    )
  })

  it('warns and continues for malformed runtime configuration', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    window.__HFL_APP_CONFIG__ = {
      sentryEnabled: true,
      sentryDsn: 'not-a-dsn',
      sentryEnvironment: 'hfl-test',
      sentryRelease: 'hyperfilelens-frontend@main-1234567',
    }
    const { initSentry } = await import('./sentry')
    await initSentry({} as App, { afterEach: vi.fn() } as unknown as Router)
    expect(sentryInit).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalled()
  })

  it('rejects a browser DSN containing a password', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    window.__HFL_APP_CONFIG__ = {
      sentryEnabled: true,
      sentryDsn: 'https://public:secret@sentry.example.com/42',
      sentryEnvironment: 'hfl-test',
      sentryRelease: 'hyperfilelens-frontend@main-1234567',
    }
    const { initSentry } = await import('./sentry')
    await initSentry({} as App, { afterEach: vi.fn() } as unknown as Router)
    expect(sentryInit).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalled()
  })

  it('uses runtime identity and removes browser PII from events', async () => {
    window.__HFL_APP_CONFIG__ = {
      sentryEnabled: true,
      sentryDsn: 'https://public@sentry.example.com/42',
      sentryEnvironment: 'hfl-preprod',
      sentryRelease: 'hyperfilelens-frontend@0.1.8',
      sentryTracesSampleRate: 0.1,
      sentrySurface: 'admin',
    }
    const { initSentry } = await import('./sentry')
    await initSentry({} as App, { afterEach: vi.fn() } as unknown as Router)

    const options = sentryInit.mock.calls[0]?.[0]
    expect(options).toEqual(expect.objectContaining({
      environment: 'hfl-preprod',
      release: 'hyperfilelens-frontend@0.1.8',
      tracesSampleRate: 0.1,
      sendDefaultPii: false,
      initialScope: { tags: expect.objectContaining({ surface: 'admin' }) },
    }))
    const event = options.beforeSend({
      user: { email: 'person@example.com' },
      request: {
        url: 'https://app.example.com/source/secret-id?token=secret#private',
        cookies: { session: 'secret' },
        data: { password: 'secret' },
        query_string: 'token=secret',
        headers: { Authorization: 'Bearer secret', 'User-Agent': 'test-browser' },
        env: { REMOTE_ADDR: '192.0.2.15', customer: 'private' },
      },
      transaction: '/source/secret-id',
      transaction_info: { source: 'url', private: 'customer' },
      spans: [{
        is_segment: true,
        segment_id: 'segment-1',
        trace_id: 'trace-span-1',
        span_id: 'span-1',
        op: 'http.client',
        description: 'https://app.example.com/api/private?token=secret',
        data: { 'http.request.header.authorization': 'secret' },
        tags: { customer: 'private' },
      }],
      message: 'customer prompt',
      logentry: { message: 'customer response' },
      extra: { args: ['/customer/private'] },
      contexts: {
        customer: { filename: 'private.txt' },
        trace: {
          trace_id: 'root-trace-1',
          span_id: 'root-span-1',
          parent_span_id: 'root-parent-1',
          op: 'navigation',
          data: { customer: 'private' },
        },
      },
      breadcrumbs: [{ category: 'console', message: 'customer path', data: { content: 'private' } }],
      exception: {
        values: [{
          value: 'open /customer/private failed',
          mechanism: { type: 'generic', data: { path: '/customer/private' } },
          stacktrace: { frames: [{ vars: { token: 'secret' } }] },
        }],
      },
    })
    expect(event.user).toBeUndefined()
    expect(event.request).toEqual({
      url: 'https://app.example.com/',
      headers: {},
    })
    expect(JSON.stringify(event)).not.toContain('secret')
    expect(JSON.stringify(event)).not.toContain('person@example.com')
    expect(event.exception.values[0].value).toBe('[Filtered]')
    expect(event.transaction).toBe('/')
    expect(event.transaction_info).toEqual({ source: 'route' })
    expect(event.contexts).toEqual({
      trace: {
        trace_id: 'root-trace-1',
        span_id: 'root-span-1',
        parent_span_id: 'root-parent-1',
        op: 'navigation',
        data: {},
      },
    })
    expect(event.spans).toEqual([{
      data: {},
      is_segment: true,
      segment_id: 'segment-1',
      trace_id: 'trace-span-1',
      span_id: 'span-1',
      op: 'http.client',
    }])
    expect(event.breadcrumbs).toEqual([{
      category: 'console',
      level: undefined,
      timestamp: undefined,
      type: undefined,
    }])
    expect(JSON.stringify(event)).not.toContain('/customer/private')
    expect(JSON.stringify(event)).not.toContain('192.0.2.15')
    expect(options.beforeSendTransaction).toBeTypeOf('function')
    expect(options.beforeSendSpan({
      is_segment: true,
      segment_id: 'segment-2',
      trace_id: 'trace-span-2',
      span_id: 'span-2',
      op: 'http.client',
      description: 'https://app.example.com/api/private?token=secret',
      data: { 'http.request.header.authorization': 'secret' },
    })).toEqual({
      data: {},
      is_segment: true,
      segment_id: 'segment-2',
      trace_id: 'trace-span-2',
      span_id: 'span-2',
      op: 'http.client',
    })
  })
})
