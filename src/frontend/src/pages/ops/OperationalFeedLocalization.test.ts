// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { defineComponent } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import AlertIncidents from './AlertIncidents.vue'
import Events from './Events.vue'
import Notifications from '../Notifications.vue'
import NotificationRecords from './NotificationRecords.vue'
import SearchPage from '../Search.vue'

const mocks = vi.hoisted(() => ({
  records: vi.fn(), statistics: vi.fn(), events: vi.fn(), notifications: vi.fn(),
  channels: vi.fn(), logs: vi.fn(), logStats: vi.fn(),
  route: { query: {} as Record<string, string> }, push: vi.fn(), replace: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))
vi.mock('../../composables/useOpsMenus', () => ({ useOpsMenus: () => [] }))
vi.mock('../../lib/alertApi', () => ({
  listRecords: mocks.records, recordStatistics: mocks.statistics,
  acknowledgeRecord: vi.fn(), resolveRecord: vi.fn(),
}))
vi.mock('../../lib/api', () => ({ api: mocks.events, apiErrorMessage: () => 'Request failed' }))
vi.mock('../../lib/notificationApi', () => ({
  listUserNotifications: mocks.notifications, markAllUserNotificationsRead: vi.fn(), markUserNotificationRead: vi.fn(),
  listChannels: mocks.channels, listLogs: mocks.logs, logStats: mocks.logStats,
}))

const id = '68b61992-8307-4cb9-a9a0-89d5bbb58ce5'
const panel = defineComponent({
  template: '<div><slot name="toolbar" /><slot name="toolbar-actions" /><slot name="toolbar-utility" /><slot name="table" :table-max-height="500" /><slot name="footer" /></div>',
})
const drawer = defineComponent({
  props: ['modelValue'],
  template: '<aside v-if="modelValue"><slot name="header" /><slot /><slot name="footer" /></aside>',
})
const wrappers: ReturnType<typeof mount>[] = []

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(resolvePromise => { resolve = resolvePromise })
  return { promise, resolve }
}

function render(component: typeof AlertIncidents | typeof Events | typeof Notifications | typeof NotificationRecords | typeof SearchPage) {
  const i18n = createI18n({
    legacy: false, locale: 'en', fallbackLocale: 'en',
    messages: {
      en,
      ...Object.fromEntries(['zh-hans', 'es'].map(locale => [locale, JSON.parse(readFileSync(
        resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8',
      ))])),
    },
  })
  const wrapper = mount(component, {
    global: {
      plugins: [ElementPlus, i18n],
      stubs: {
        ModulePage: { template: '<main><slot /></main>' },
        HflTablePanel: panel, HflPagination: true, ElDrawer: drawer,
      },
      directives: { 'table-column-resize': () => undefined, 'table-overflow-title': () => undefined },
    },
  })
  wrappers.push(wrapper)
  return { wrapper, i18n }
}

describe('operational feed rendered localization', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.statistics.mockResolvedValue({ critical: 0, warning: 1, firing: 1, acknowledged: 0 })
    mocks.channels.mockResolvedValue({ results: [] })
    mocks.logStats.mockResolvedValue({ total: 1, success: 1, failed: 0, success_rate: 100 })
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  })
  afterEach(() => {
    wrappers.splice(0).forEach(wrapper => wrapper.unmount())
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('localizes delivery rows and the open delivery detail title', async () => {
    mocks.logs.mockResolvedValue({ count: 1, results: [{
      id: '1', status: 'success', notification_type: 'firing',
      alert: { type: 'task', title: 'Task alert: task_failed', resource_name: 'Backup prod-myteamone' },
    }] })
    const { wrapper, i18n } = render(NotificationRecords)
    await flushPromises()
    await wrapper.get('.hfl-table-name-link').trigger('click')
    for (const locale of ['en', 'zh-hans', 'es']) {
      i18n.global.locale.value = locale
      await flushPromises()
      const { t } = i18n.global
      const title = t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_failed') })
      expect(wrapper.get('.hfl-table-name-link').text()).toBe(title)
      expect(wrapper.get('aside').text()).toContain(title)
      expect(wrapper.get('aside').text()).toContain('Backup prod-myteamone')
    }
    expect(mocks.logs).toHaveBeenCalledOnce()
  })

  it.each([
    ['alert', AlertIncidents], ['delivery', NotificationRecords],
  ])('sends translated %s search aliases to both the list and statistics endpoints', async (_surface, component) => {
    vi.useFakeTimers()
    mocks.records.mockResolvedValue({ count: 0, results: [] })
    mocks.logs.mockResolvedValue({ count: 0, results: [] })
    const { wrapper, i18n } = render(component)
    i18n.global.locale.value = 'zh-hans'
    await flushPromises()
    const query = i18n.global.t('ops.alertsCenter.taskEvents.task_failed')
    await wrapper.get('input.hfl-list-search__input, .hfl-list-search input').setValue(query)
    await vi.advanceTimersByTimeAsync(950)
    await flushPromises()
    const requests = component === AlertIncidents ? [mocks.records, mocks.statistics] : [mocks.logs, mocks.logStats]
    for (const request of requests) {
      const params = request.mock.calls.at(-1)![0]
      expect(params.search).toBe(query)
      expect(JSON.parse(params.search_aliases)).toContain('Task alert: task_failed')
    }
  })

  it('sends translated event aliases to the server rather than filtering the current page', async () => {
    vi.useFakeTimers()
    mocks.events.mockResolvedValue({ count: 0, results: [], stats: {} })
    const { wrapper, i18n } = render(Events)
    i18n.global.locale.value = 'zh-hans'
    await flushPromises()
    const query = i18n.global.t('ops.events.taskTerminal.failed', {
      type: i18n.global.t('ops.task.taskType.backup'), name: '',
    })
    await wrapper.get('.hfl-list-search input').setValue(query)
    await vi.advanceTimersByTimeAsync(400)
    await flushPromises()
    const params = new URL(mocks.events.mock.calls.at(-1)![0], 'https://fixture.test').searchParams
    expect(params.get('search')).toBe(query.trim())
    expect(JSON.parse(params.get('search_aliases')!)).toContain('Backup failed:')
  })

  it('searches translated alert titles on the server in global search and renders matching results', async () => {
    vi.useFakeTimers()
    mocks.events.mockImplementation(async (path: string) => path.includes('/alerts/records/')
      ? { count: 1, results: [{ id: '1', type: 'task', title: 'Task alert: task_failed', status: 'firing' }] }
      : { results: [] })
    const { wrapper, i18n } = render(SearchPage)
    i18n.global.locale.value = 'zh-hans'
    await flushPromises()
    const query = i18n.global.t('ops.alertsCenter.taskEvents.task_failed')
    await wrapper.get('input').setValue(query)
    await vi.advanceTimersByTimeAsync(950)
    await flushPromises()
    const path = mocks.events.mock.calls.filter(([url]) => url.includes('/alerts/records/')).at(-1)![0]
    const params = new URL(path, 'https://fixture.test').searchParams
    expect(params.get('search_context')).toBe('global')
    expect(params.get('search')).toBe(query)
    expect(JSON.parse(params.get('search_aliases')!)).toContain('Task alert: task_failed')
    expect(wrapper.text()).toContain(i18n.global.t('ops.events.taskAlert', { event: query }))
  })

  it('shows loading while a global alert search is pending and ignores stale responses', async () => {
    vi.useFakeTimers()
    const older = deferred<{ results: object[] }>()
    const newer = deferred<{ results: object[] }>()
    let requests = 0
    mocks.events.mockImplementation((path: string) => path.includes('/alerts/records/')
      ? (++requests === 1 ? older.promise : newer.promise)
      : Promise.resolve({ results: [] }))
    const { wrapper } = render(SearchPage)
    await flushPromises()
    expect(wrapper.get('.el-loading-mask').attributes('style') || '').not.toContain('display: none')
    await wrapper.get('input').setValue('timeout')
    await vi.advanceTimersByTimeAsync(950)
    newer.resolve({ results: [{ id: '2', type: 'task', title: 'Task alert: task_timeout', status: 'firing' }] })
    await flushPromises()
    expect(wrapper.findAll('.el-table').at(-1)!.text()).toContain('Task Timeout')
    older.resolve({ results: [{ id: '1', type: 'task', title: 'Stale task alert', status: 'firing' }] })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Stale task alert')
    expect(wrapper.get('.el-loading-mask').attributes('style')).toContain('display: none')
  })

  it('shows a retryable global alert search error and recovers on retry', async () => {
    let failed = true
    mocks.events.mockImplementation(async (path: string) => {
      if (!path.includes('/alerts/records/')) return { results: [] }
      if (failed) throw new Error('Search unavailable')
      return { results: [{ id: '2', type: 'task', title: 'Task alert: task_timeout', status: 'firing' }] }
    })
    const { wrapper } = render(SearchPage)
    await flushPromises()
    expect(wrapper.find('.hfl-error-state').exists()).toBe(true)
    failed = false
    await wrapper.get('.hfl-error-state__retry').trigger('click')
    await flushPromises()
    expect(wrapper.find('.hfl-error-state').exists()).toBe(false)
    expect(wrapper.findAll('.el-table').at(-1)!.text()).toContain('Task Timeout')
  })

  it('updates alert rows and an open details drawer without refetching or rewriting records', async () => {
    const record = {
      id: '1', type: 'task', severity: 'warning', status: 'firing',
      title: 'Task alert: task_timeout', message: `Task ${id} status=timeout`,
      resource_name: 'Backup prod-myteamone',
    }
    mocks.records.mockResolvedValue({ count: 1, results: [record] })
    const { wrapper, i18n } = render(AlertIncidents)
    await flushPromises()
    await wrapper.get('.hfl-table-name-link').trigger('click')
    for (const locale of ['en', 'zh-hans', 'es']) {
      i18n.global.locale.value = locale
      await flushPromises()
      const { t } = i18n.global
      const title = t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_timeout') })
      const message = t('ops.events.taskDiagnostic.taskStatus', { id, status: t('ops.task.status.timeout') })
      expect(wrapper.get('.hfl-table-name-link').text()).toBe(title)
      expect(wrapper.get('aside').text()).toContain(title)
      expect(wrapper.get('aside').text()).toContain(message)
      expect(wrapper.get('aside').text()).toContain(record.resource_name)
      expect(wrapper.get('.hfl-ops-cell-stack__meta').text()).toBe(message)
    }
    expect(mocks.records).toHaveBeenCalledOnce()
    expect(mocks.statistics).toHaveBeenCalledOnce()
    expect(record.message).toBe(`Task ${id} status=timeout`)
  })

  it('updates event rows and an open details drawer while preserving the source path', async () => {
    const name = 'Backup prod-myteamone'
    const source = 'root@prod-myteamone:/opt/seedone'
    const event = {
      id: '1', event_type: 'task.failed', category: 'protection', severity: 'warning',
      title: `Backup failed: ${name}`, details: `upload error: unsupported source: ${source}`,
      resource_type: 'task', resource_name: name,
    }
    mocks.events.mockResolvedValue({ count: 1, results: [event], stats: { total: 1, warning: 1 } })
    const { wrapper, i18n } = render(Events)
    await flushPromises()
    await wrapper.get('.hfl-table-name-link').trigger('click')
    for (const locale of ['en', 'zh-hans', 'es']) {
      i18n.global.locale.value = locale
      await flushPromises()
      const { t } = i18n.global
      const title = t('ops.events.taskTerminal.failed', { type: t('ops.task.taskType.backup'), name })
      expect(wrapper.get('.hfl-table-name-link').text()).toBe(title)
      expect(wrapper.get('aside').text()).toContain(title)
      expect(wrapper.get('aside').text()).toContain(t('ops.events.taskDiagnostic.unsupportedSource', { source }))
    }
    expect(mocks.events).toHaveBeenCalledOnce()
    expect(event.details).toBe(`upload error: unsupported source: ${source}`)
  })

  it('localizes resolved alerts in the full notification inbox without refetching', async () => {
    const notification = {
      id: '1', kind: 'alert', title: 'Resolved: Task alert: task_failed',
      summary: `Task ${id} status=failed`, severity: 'warning', is_read: false, to: '/ops/alerts',
    }
    mocks.notifications.mockResolvedValue({ count: 1, unread_count: 1, results: [notification] })
    const { wrapper, i18n } = render(Notifications)
    await flushPromises()
    for (const locale of ['en', 'zh-hans', 'es']) {
      i18n.global.locale.value = locale
      await flushPromises()
      const { t } = i18n.global
      const row = wrapper.get('.notifications-page__link')
      expect(row.get('strong').text()).toBe(t('ops.events.resolvedTaskAlert', {
        title: t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_failed') }),
      }))
      expect(row.get('span').text()).toBe(t('ops.events.taskDiagnostic.taskStatus', {
        id, status: t('ops.task.status.failed'),
      }))
    }
    expect(mocks.notifications).toHaveBeenCalledOnce()
    expect(notification.title).toBe('Resolved: Task alert: task_failed')
  })
})
