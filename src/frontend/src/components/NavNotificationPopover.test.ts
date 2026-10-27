// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../locales/en'
import NavNotificationPopover from './NavNotificationPopover.vue'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  markAll: vi.fn(),
  markRead: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('../lib/notificationApi', () => ({
  listUserNotifications: mocks.list,
  markAllUserNotificationsRead: mocks.markAll,
  markUserNotificationRead: mocks.markRead,
}))

const HflPopoverStub = defineComponent({
  emits: ['update:visible'],
  template: `
    <div>
      <button data-test="open-popover" @click="$emit('update:visible', true)">
        <slot name="reference" />
      </button>
      <slot />
    </div>
  `,
})

const ElButtonStub = defineComponent({
  template: '<button @click="$emit(\'click\')"><slot /></button>',
})

function notification(id: string, isRead = false) {
  return {
    id,
    kind: 'alert',
    title: `Notification ${id}`,
    summary: 'Repository offline',
    severity: 'critical',
    updated_at: '2026-08-12T00:00:00Z',
    is_read: isRead,
    to: '/ops/alerts',
  }
}

function response(results = [notification('1')], unreadCount = 1) {
  return { count: results.length, unread_count: unreadCount, results }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function testI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: {
      en,
      ...Object.fromEntries(['zh-hans', 'es'].map(locale => [locale, JSON.parse(readFileSync(
        resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8',
      ))])),
    },
    missingWarn: false,
    fallbackWarn: false,
  })
}

function mountPopover(i18n = testI18n()) {
  return mount(NavNotificationPopover, {
    global: {
      plugins: [i18n],
      stubs: { HflPopover: HflPopoverStub, ElButton: ElButtonStub },
    },
  })
}

describe('NavNotificationPopover', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.list.mockResolvedValue(response())
    mocks.markAll.mockResolvedValue(undefined)
    mocks.markRead.mockResolvedValue(undefined)
    mocks.push.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders firing and resolved task alerts and switches locales without refetching', async () => {
    const id = '68b61992-8307-4cb9-a9a0-89d5bbb58ce5'
    const firing = { ...notification('1'), title: 'Task alert: task_failed', summary: `Task ${id} status=failed` }
    const resolved = { ...notification('2'), title: 'Resolved: Task alert: task_timeout', summary: 'Backup prod-myteamone' }
    mocks.list.mockResolvedValue(response([firing, resolved], 2))
    const i18n = testI18n()
    const wrapper = mountPopover(i18n)
    await flushPromises()
    for (const locale of ['en', 'zh-hans', 'es']) {
      i18n.global.locale.value = locale
      await flushPromises()
      const { t } = i18n.global
      expect(wrapper.findAll('.nn-item-title').map(item => item.text())).toEqual([
        t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_failed') }),
        t('ops.events.resolvedTaskAlert', {
          title: t('ops.events.taskAlert', { event: t('ops.alertsCenter.taskEvents.task_timeout') }),
        }),
      ])
      expect(wrapper.findAll('.nn-item-summary').map(item => item.text())).toEqual([
        t('ops.events.taskDiagnostic.taskStatus', { id, status: t('ops.task.status.failed') }),
        resolved.summary,
      ])
    }
    expect(mocks.list).toHaveBeenCalledOnce()
    expect(firing.title).toBe('Task alert: task_failed')
    expect(firing.summary).toBe(`Task ${id} status=failed`)
    await wrapper.findAll('.nn-item')[1]!.trigger('click')
    await flushPromises()
    expect(mocks.markRead).toHaveBeenCalledWith('2')
    expect(mocks.push).toHaveBeenCalledWith('/ops/alerts')
    wrapper.unmount()
  })

  it('uses the unread count for the badge and opens the notification target', async () => {
    mocks.list.mockResolvedValue(response([notification('1'), notification('2', true)], 1))
    const wrapper = mountPopover()
    await flushPromises()

    expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    await wrapper.get('.nn-item').trigger('click')
    await flushPromises()

    expect(mocks.markRead).toHaveBeenCalledWith('1')
    expect(mocks.push).toHaveBeenCalledWith('/ops/alerts')
    expect(wrapper.find('.nav-notification-badge').exists()).toBe(false)
    wrapper.unmount()
  })

  it('routes View All to the personal notification inbox', async () => {
    const wrapper = mountPopover()
    await flushPromises()

    await wrapper.get('.nav-dropdown-panel__foot-link').trigger('click')

    expect(mocks.push).toHaveBeenCalledWith('/account/notifications')
    wrapper.unmount()
  })

  it('keeps existing notifications when a refresh fails', async () => {
    const wrapper = mountPopover()
    await flushPromises()
    mocks.list.mockRejectedValueOnce(new Error('network unavailable'))

    await wrapper.get('[data-test="open-popover"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.nn-item-title').text()).toBe('Notification 1')
    expect(wrapper.get('.nn-load-error').text()).toContain('Failed to load notifications')
    wrapper.unmount()
  })

  it('does not let an older request overwrite a newer response', async () => {
    const older = deferred<ReturnType<typeof response>>()
    const newer = deferred<ReturnType<typeof response>>()
    mocks.list.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    const wrapper = mountPopover()

    await wrapper.get('[data-test="open-popover"]').trigger('click')
    newer.resolve(response([notification('new')], 1))
    await flushPromises()
    older.resolve(response([notification('old')], 1))
    await flushPromises()

    expect(wrapper.get('.nn-item-title').text()).toBe('Notification new')
    expect(wrapper.text()).not.toContain('Notification old')
    wrapper.unmount()
  })
})
