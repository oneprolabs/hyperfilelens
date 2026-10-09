// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import type { PlatformIntegration, SourceLensRuntimeMonitor } from '../lib/platformOpsApi'
import RuntimeServiceConnections from './RuntimeServiceConnections.vue'
import EnvironmentSettings from '../pages/platform/settings/EnvironmentSettings.vue'

const { fetchIntegrations, fetchEnvironment } = vi.hoisted(() => ({
  fetchIntegrations: vi.fn(),
  fetchEnvironment: vi.fn(),
}))
vi.mock('../lib/platformOpsApi', () => ({
  fetchPlatformIntegrations: fetchIntegrations,
  fetchPlatformEnvironment: fetchEnvironment,
}))
vi.mock('../composables/useResolvedPlatformOpsSideNav', () => ({
  useResolvedPlatformOpsSideNav: () => [],
}))

const ElTag = defineComponent({
  props: ['type', 'size', 'effect'],
  template: '<span class="status-tag" :data-tone="type" :data-effect="effect"><slot /></span>',
})
const ModulePage = defineComponent({ template: '<main><slot /></main>' })
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function integration(monitor?: SourceLensRuntimeMonitor): PlatformIntegration {
  return {
    key: 'sourcelens', name: 'SourceLens', category: 'AI and data services',
    mode: 'bundled', version: '1', base_url: 'http://sl', gateway_base_url: '',
    console_url: '', configured: true, reachable: true, authenticated: true,
    business_ready: true, status: 'ready', warning: '', managed_by: 'deployment',
    checked_at: '2026-10-08T06:00:00+00:00', runtime_monitor: monitor,
  }
}

function monitor(): SourceLensRuntimeMonitor {
  return {
    status: 'degraded', health_status: 'error', checked_at: '2026-10-08T06:00:00+00:00',
    components: {
      postgres: {
        health_status: 'error', availability_status: 'unknown',
        notices: [{
          code: 'index_corruption', level: 'error',
          params: { last_seen_at: '2026-10-08T05:30:00+00:00' },
        }],
      },
      redis: {
        health_status: 'ok', availability_status: 'degraded',
        notices: [{
          code: 'queue_backlog', level: 'warning',
          params: { queue: 'lens', count: 144307, threshold: 1000 },
        }],
      },
    },
  }
}

async function render(row: PlatformIntegration) {
  fetchIntegrations.mockResolvedValue({ integrations: [row] })
  const wrapper = mount(RuntimeServiceConnections, {
    global: {
      plugins: [i18n], stubs: { ElTag, ElEmpty: true },
      directives: { loading: () => {} },
    },
  })
  await flushPromises()
  return wrapper
}

describe('SourceLens Runtime Environment presentation', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reuses the three status columns, light tags and existing inline notice classes', async () => {
    const wrapper = await render(integration(monitor()))
    const heading = wrapper.get('.runtime-status-table__head').text()
    expect(heading).toContain('Runtime State')
    expect(heading).toContain('Health Check')
    expect(heading).toContain('Business Availability')
    const rows = wrapper.findAll('.runtime-status-table__row')
    const postgres = rows.find(row => row.get('.runtime-status-table__service').text() === 'PostgreSQL')!
    expect(postgres.findAll('.status-tag').map(tag => tag.text())).toEqual([
      'Not Monitored', 'Unhealthy', '—',
    ])
    expect(postgres.findAll('.status-tag')[1]!.attributes('data-tone')).toBe('danger')
    expect(postgres.get('.runtime-status-table__notice--error').text()).toContain('index corruption')
    const redis = rows.find(row => row.get('.runtime-status-table__service').text() === 'Redis')!
    expect(redis.findAll('.status-tag').map(tag => tag.text())).toEqual([
      'Not Monitored', 'Healthy', 'Degraded',
    ])
    expect(redis.get('.runtime-status-table__notice--warning').text()).toContain('144307')
    expect(rows[0]!.findAll('.status-tag').map(tag => tag.text())).toEqual([
      'Not Monitored', 'Unhealthy', 'Degraded',
    ])
    const api = rows.find(row => row.get('.runtime-status-table__service').text() === 'API')!
    expect(api.text()).toContain('Healthy')
    expect(api.text()).toContain('Operational')
    const worker = rows.find(row => row.get('.runtime-status-table__service').text() === 'Worker')!
    expect(worker.findAll('.status-tag').map(tag => tag.text())).toEqual([
      'Not Monitored', 'Not Monitored', '—',
    ])
    wrapper.findAll('.status-tag').forEach(tag => {
      expect(tag.attributes('data-effect')).toBe('light')
    })
    expect(wrapper.text()).not.toContain('Chat ID')
    expect(wrapper.text()).not.toContain('Task ID')
    wrapper.unmount()
  })

  it('shows historical evidence as information, not an active database failure', async () => {
    const snapshot = monitor()
    snapshot.status = 'ok'
    snapshot.health_status = 'ok'
    snapshot.components = {
      postgres: {
        health_status: 'unknown', availability_status: 'unknown',
        notices: [{
          code: 'index_corruption_history', level: 'info',
          params: { last_seen_at: '2026-09-30T18:00:00+00:00' },
        }],
      },
    }
    const wrapper = await render(integration(snapshot))
    expect(wrapper.find('.runtime-status-table__notice--error').exists()).toBe(false)
    expect(wrapper.get('.runtime-status-table__notice--info').text()).toContain('Historical')
    const postgres = wrapper.findAll('.runtime-status-table__row').find(
      row => row.get('.runtime-status-table__service').text() === 'PostgreSQL',
    )!
    expect(postgres.findAll('.status-tag')[1]!.text()).toBe('Not Monitored')
    wrapper.unmount()
  })

  it('keeps unconfigured metrics unmonitored instead of showing zero or Healthy', async () => {
    const snapshot = monitor()
    snapshot.components.redis = {
      health_status: 'unknown', availability_status: 'unknown',
      notices: [{ code: 'queue_not_configured', level: 'info', params: {} }],
    }
    const wrapper = await render(integration(snapshot))
    const redis = wrapper.findAll('.runtime-status-table__row').find(
      row => row.get('.runtime-status-table__service').text() === 'Redis',
    )!
    expect(redis.findAll('.status-tag').map(tag => tag.text())).toEqual([
      'Not Monitored', 'Not Monitored', '—',
    ])
    expect(redis.get('.runtime-status-table__notice--info').text()).toContain('not configured')
    wrapper.unmount()
  })

  it('preserves the old API-only response contract', async () => {
    const wrapper = await render(integration())
    expect(wrapper.find('.runtime-status-table__notice--error').exists()).toBe(false)
    expect(wrapper.findAll('.runtime-status-table__row')[0]!.text()).toContain('Operational')
    wrapper.unmount()
  })

  it('shows actual queue counts and collection time in existing Details below the threshold', async () => {
    const snapshot = monitor()
    snapshot.status = 'ok'
    snapshot.health_status = 'ok'
    snapshot.components = {
      redis: {
        health_status: 'ok', availability_status: 'unknown',
        queue_lengths: { lens: 12, sourcelens: 0 }, checked_at: '2026-10-09T02:00:00+00:00',
        notices: [],
      },
    }
    const wrapper = await render(integration(snapshot))
    const redis = wrapper.findAll('.runtime-status-table__row').find(
      row => row.get('.runtime-status-table__service').text() === 'Redis',
    )!
    expect(redis.get('.runtime-status-table__details').text()).toContain('Queue "lens": 12 pending messages.')
    expect(redis.get('.runtime-status-table__details').text()).toContain('Queue "sourcelens": 0 pending messages.')
    expect(redis.text()).toContain(en.platformOps.settings.environment.checkedAt)
    expect(redis.find('.runtime-status-table__notice--warning').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not repeat backlog counts in ordinary Details and the same warning', async () => {
    const snapshot = monitor()
    snapshot.components.redis!.queue_lengths = { lens: 144307 }
    snapshot.components.redis!.checked_at = '2026-10-09T02:00:00+00:00'
    const wrapper = await render(integration(snapshot))
    const redis = wrapper.findAll('.runtime-status-table__row').find(
      row => row.get('.runtime-status-table__service').text() === 'Redis',
    )!
    expect(redis.text().match(/144307/g)).toHaveLength(1)
    expect(redis.get('.runtime-status-table__notice--warning').text()).toContain('144307')
    wrapper.unmount()
  })

  it('shows unconfirmed recovery as an existing warning instead of an all-green summary', async () => {
    const snapshot = monitor()
    snapshot.health_status = 'degraded'
    snapshot.components = {
      postgres: {
        health_status: 'unknown', availability_status: 'unknown',
        notices: [{
          code: 'index_corruption_unconfirmed', level: 'warning',
          params: { last_seen_at: '2026-10-06T05:00:00+00:00' },
        }],
      },
    }
    const wrapper = await render(integration(snapshot))
    const rows = wrapper.findAll('.runtime-status-table__row')
    expect(rows[0]!.findAll('.status-tag')[2]!.text()).toBe('Degraded')
    expect(rows[0]!.get('.runtime-status-table__notice--warning').text()).toContain(
      'recovery has not been confirmed',
    )
    expect(wrapper.find('.runtime-status-table__notice--error').exists()).toBe(false)
    wrapper.unmount()
  })

  it('reports incomplete scans with the existing warning style', async () => {
    const snapshot = monitor()
    snapshot.health_status = 'degraded'
    snapshot.components = {
      postgres: {
        health_status: 'unknown', availability_status: 'unknown',
        notices: [{ code: 'index_scan_incomplete', level: 'warning', params: {} }],
      },
    }
    const wrapper = await render(integration(snapshot))
    expect(wrapper.get('.runtime-status-table__notice--warning').text()).toContain(
      'log scan is incomplete',
    )
    expect(wrapper.findAll('.runtime-status-table__row')[0]!.text()).toContain('Degraded')
    wrapper.unmount()
  })

  it('keeps Redis Healthy when its queue metrics cannot be read', async () => {
    const snapshot = monitor()
    snapshot.health_status = 'degraded'
    snapshot.components = {
      redis: {
        health_status: 'ok', availability_status: 'unknown',
        notices: [{
          code: 'queue_metrics_unavailable', level: 'warning', params: { queue: 'lens' },
        }],
      },
    }
    const wrapper = await render(integration(snapshot))
    const redis = wrapper.findAll('.runtime-status-table__row').find(
      row => row.get('.runtime-status-table__service').text() === 'Redis',
    )!
    expect(redis.findAll('.status-tag').map(tag => tag.text())).toEqual([
      'Not Monitored', 'Healthy', '—',
    ])
    expect(redis.get('.runtime-status-table__notice--warning').text()).toContain(
      'check monitoring permissions',
    )
    wrapper.unmount()
  })

  it('propagates runtime degradation to the existing Instance Health tag', async () => {
    fetchIntegrations.mockResolvedValue({ integrations: [integration(monitor())] })
    fetchEnvironment.mockResolvedValue({
      app_version: '1', agent_version: '1', django_debug: false, effective: {}, sources: {},
      health: {
        api: { status: 'ok' }, database: { status: 'ok' }, redis: { status: 'ok' },
        celery: { status: 'ok', worker_count: 1 }, scheduler: { status: 'ok' },
        nginx: { status: 'ok' }, web: { status: 'ok' },
        sourcelens: { status: 'degraded' }, data_gateway: { status: 'not_configured' },
      },
    })
    const wrapper = mount(EnvironmentSettings, {
      global: {
        plugins: [i18n], stubs: { ElTag, ModulePage, ElEmpty: true },
        directives: { loading: () => {} },
      },
    })
    await flushPromises()
    const health = wrapper.findAll('.platform-settings__overview-item').find(
      item => item.text().includes(en.platformOps.settings.environment.instanceHealth),
    )!
    expect(health.get('.status-tag').text()).toBe('Degraded')
    expect(health.get('.status-tag').attributes('data-tone')).toBe('warning')
    wrapper.unmount()
  })
})
