// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../locales/en'
import NavNotificationPopover from './NavNotificationPopover.vue'
import { notificationScope, onNotificationReadChange, publishNotificationReadChange } from '../lib/notificationReadState'
import { currentUser } from '../composables/useAuth'

vi.mock('../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref({ id: 1, access_profile: { org_key: 'test-org' } }) }
})

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
  props: ['disabled', 'loading'],
  template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
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
    sessionStorage.clear()
    localStorage.clear()
    currentUser.value = { id: 1, email: 'test@example.com', username: 'test', access_profile: { org_key: 'test-org' } } as typeof currentUser.value
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
    expect(mocks.markRead).toHaveBeenCalledWith('2', 'test-org')
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

    expect(mocks.markRead).toHaveBeenCalledWith('1', 'test-org')
    expect(mocks.push).toHaveBeenCalledWith('/ops/alerts')
    expect(wrapper.find('.nav-notification-badge').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows no zero count and does not mistake severity for unread state', async () => {
    mocks.list.mockResolvedValue(response([notification('1', true)], 0))
    const wrapper = mountPopover()
    await flushPromises()
    expect(wrapper.get('.nav-dropdown-panel__title').text()).toBe('Notifications')
    expect(wrapper.find('.nn-unread-count').exists()).toBe(false)
    expect(wrapper.find('.nn-unread-dot').exists()).toBe(false)
    expect(wrapper.find('.notification-outcome-icon--error').exists()).toBe(true)
    expect(wrapper.find('.nn-item.is-unread').exists()).toBe(false)
    expect(wrapper.get('.nn-item').attributes('aria-label')).toContain('Read')
    wrapper.unmount()
  })

  it('keeps opening read-only and uses separate summary and time lines', async () => {
    const wrapper = mountPopover()
    await flushPromises()
    await wrapper.get('[data-test="open-popover"]').trigger('click')
    await flushPromises()
    expect(mocks.markRead).not.toHaveBeenCalled()
    expect(mocks.markAll).not.toHaveBeenCalled()
    expect(wrapper.find('.nn-unread-count').exists()).toBe(false)
    expect(wrapper.get('.nav-dropdown-panel__title').text()).toBe('Notifications')
    expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    expect(wrapper.find('.nn-item-sep').exists()).toBe(false)
    expect(wrapper.get('.nn-item-summary').text()).toBe('Repository offline')
    expect(wrapper.get('.nn-item-time').text()).not.toBe('')
    wrapper.unmount()
  })

  it('uses a MailCheck action and disables it while marking all read', async () => {
    const pending = deferred<void>()
    mocks.markAll.mockReturnValueOnce(pending.promise)
    const wrapper = mountPopover()
    await flushPromises()
    const action = wrapper.get('.nn-mark-all')
    expect(action.element.tagName).toBe('BUTTON')
    expect(action.classes()).toContain('nav-dropdown-panel__foot-link')
    expect(action.classes()).not.toContain('el-button')
    expect(action.text()).toBe('Mark All Read')
    expect(action.find('.lucide-mail-check').exists()).toBe(true)
    await action.trigger('click')
    expect(action.attributes('disabled')).toBeDefined()
    expect(action.attributes('aria-busy')).toBe('true')
    expect(action.find('.nn-mark-all__loading').exists()).toBe(true)
    await action.trigger('click')
    expect(mocks.markAll).toHaveBeenCalledOnce()
    pending.resolve()
    await flushPromises()
    expect(wrapper.find('.nn-mark-all').exists()).toBe(false)
    expect(wrapper.find('.nav-notification-badge').exists()).toBe(false)
    wrapper.unmount()
  })

  it('immediately applies inbox read changes and ignores an older polling response', async () => {
    const wrapper = mountPopover()
    await flushPromises()
    const older = deferred<ReturnType<typeof response>>()
    mocks.list.mockReturnValueOnce(older.promise)
    await wrapper.get('[data-test="open-popover"]').trigger('click')
    mocks.list.mockResolvedValueOnce(response([notification('1', true)], 0))
    publishNotificationReadChange({ scope: notificationScope(), all: true })
    await flushPromises()
    expect(wrapper.find('.nav-notification-badge').exists()).toBe(false)
    expect(wrapper.find('.nn-item.is-unread').exists()).toBe(false)
    older.resolve(response([notification('1')], 1))
    await flushPromises()
    expect(wrapper.find('.nav-notification-badge').exists()).toBe(false)
    expect(wrapper.find('.nn-item.is-unread').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not clear unread state when marking all read fails', async () => {
    const wrapper = mountPopover()
    await flushPromises()
    mocks.markAll.mockRejectedValueOnce(new Error('Unavailable'))
    await wrapper.get('.nav-dropdown-panel__head-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    expect(wrapper.get('.nn-load-error').text()).toContain('Failed to update')
    wrapper.unmount()
  })

  it('does not publish a late mutation after the component has unmounted', async () => {
    const pending = deferred<void>()
    mocks.markAll.mockReturnValueOnce(pending.promise)
    const wrapper = mountPopover()
    await flushPromises()
    const listener = vi.fn()
    const unsubscribe = onNotificationReadChange(listener)
    await wrapper.get('.nav-dropdown-panel__head-action').trigger('click')
    wrapper.unmount()
    pending.resolve()
    await flushPromises()
    expect(listener).not.toHaveBeenCalled()
    unsubscribe()
  })

  it.each(['organization', 'user'])('ignores late mark-all completion after a %s change', async (kind) => {
    const pending = deferred<void>()
    mocks.markAll.mockReturnValueOnce(pending.promise)
    const wrapper = mountPopover()
    try {
      await flushPromises()
      await wrapper.get('.nn-mark-all').trigger('click')
      mocks.list.mockResolvedValueOnce(response([notification('new-scope')], 1))
      currentUser.value = {
        ...currentUser.value!,
        ...(kind === 'user' ? { id: 2 } : {}),
        access_profile: {
          ...currentUser.value!.access_profile!,
          org_key: kind === 'organization' ? 'other-org' : 'test-org',
        },
      }
      await flushPromises()
      expect(wrapper.get('.nn-item-title').text()).toBe('Notification new-scope')
      pending.resolve()
      await flushPromises()
      expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
      expect(wrapper.find('.nn-item.is-unread').exists()).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('does not navigate to an old notification target after the organization changes', async () => {
    const pending = deferred<void>()
    mocks.markRead.mockReturnValueOnce(pending.promise)
    const wrapper = mountPopover()
    try {
      await flushPromises()
      await wrapper.get('.nn-item').trigger('click')
      mocks.list.mockResolvedValueOnce(response([notification('new-org')], 1))
      currentUser.value = { ...currentUser.value!, access_profile: { ...currentUser.value!.access_profile!, org_key: 'other-org' } }
      await flushPromises()
      pending.resolve()
      await flushPromises()
      expect(mocks.push).not.toHaveBeenCalled()
      expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    } finally {
      wrapper.unmount()
    }
  })

  it('does not surface an old-scope read error in the new organization', async () => {
    const pending = deferred<void>()
    mocks.markAll.mockReturnValueOnce(pending.promise)
    const wrapper = mountPopover()
    try {
      await flushPromises()
      await wrapper.get('.nn-mark-all').trigger('click')
      mocks.list.mockResolvedValueOnce(response([notification('new-org')], 1))
      currentUser.value = { ...currentUser.value!, access_profile: { ...currentUser.value!.access_profile!, org_key: 'other-org' } }
      await flushPromises()
      pending.reject(new Error('Old organization failed'))
      await flushPromises()
      expect(wrapper.find('.nn-load-error').exists()).toBe(false)
      expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    } finally {
      wrapper.unmount()
    }
  })

  it('ignores a previous organization list response after the new organization has loaded', async () => {
    const pending = deferred<ReturnType<typeof response>>()
    mocks.list.mockReturnValueOnce(pending.promise)
    const wrapper = mountPopover()
    try {
      mocks.list.mockResolvedValueOnce(response([notification('new-org')], 1))
      currentUser.value = { ...currentUser.value!, access_profile: { ...currentUser.value!.access_profile!, org_key: 'other-org' } }
      await flushPromises()
      pending.resolve(response([notification('old-org')], 7))
      await flushPromises()
      expect(wrapper.get('.nn-item-title').text()).toBe('Notification new-org')
      expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    } finally {
      wrapper.unmount()
    }
  })

  it('does not mark a different organization read while the displayed inbox is stale', async () => {
    const wrapper = mountPopover()
    try {
      await flushPromises()
      sessionStorage.setItem('hfl_org_key', 'new-org')
      mocks.list.mockResolvedValueOnce(response([notification('new-org')], 1))
      await wrapper.get('.nn-mark-all').trigger('click')
      await flushPromises()
      expect(mocks.markAll).not.toHaveBeenCalled()
      expect(mocks.list).toHaveBeenLastCalledWith(10, 1, 'new-org')
      expect(wrapper.get('.nn-item-title').text()).toBe('Notification new-org')
    } finally {
      wrapper.unmount()
    }
  })

  it('omits the summary instead of showing a placeholder when no summary exists', async () => {
    mocks.list.mockResolvedValue(response([{ ...notification('1'), summary: '' }]))
    const wrapper = mountPopover()
    await flushPromises()
    expect(wrapper.find('.nn-item-summary').exists()).toBe(false)
    expect(wrapper.get('.nn-item-time').exists()).toBe(true)
    wrapper.unmount()
  })

  it('routes View All to the personal notification inbox', async () => {
    const wrapper = mountPopover()
    await flushPromises()

    await wrapper.get('.nav-dropdown-panel__foot .nav-dropdown-panel__foot-link').trigger('click')

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
