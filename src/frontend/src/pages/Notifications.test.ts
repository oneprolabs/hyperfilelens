// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { computed, defineComponent, inject, provide, type ComputedRef } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../locales/en'
import Notifications from './Notifications.vue'
import NavNotificationPopover from '../components/NavNotificationPopover.vue'
import type { UserNotification } from '../lib/notificationApi'
import { notificationScope, publishNotificationReadChange } from '../lib/notificationReadState'
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

const HflTablePanelStub = defineComponent({
  template: `
    <div>
      <slot name="toolbar" />
      <slot name="toolbar-utility" />
      <slot name="table" :table-max-height="500" />
      <slot name="footer" />
    </div>
  `,
})

const HflPaginationStub = defineComponent({
  props: ['currentPage'],
  emits: ['update:currentPage'],
  template: '<button data-test="pagination" @click="$emit(\'update:currentPage\', 2)">Page {{ currentPage }}</button>',
})

const ElTableStub = defineComponent({
  props: ['data', 'rowClassName'],
  setup(props) {
    provide('notificationTestRows', computed(() => props.data))
  },
  template: `
    <div>
      <button
        v-for="row in data"
        :key="row.id"
        class="notification-row"
        @click="$emit('open', row)"
      >{{ row.title }} {{ row.is_read ? 'Read' : 'Unread' }}</button>
      <slot />
    </div>
  `,
})

const ElTableColumnStub = defineComponent({
  props: ['label', 'width'],
  setup() {
    return { rows: inject<ComputedRef<UserNotification[]>>('notificationTestRows') }
  },
  template: `
    <section class="notification-column" :data-label="label">
      <div v-for="row in rows" :key="row.id" class="notification-cell">
        <slot :row="row" />
      </div>
    </section>
  `,
})
const ElEmptyStub = defineComponent({ template: '<div />' })
const ElButtonStub = defineComponent({
  props: ['disabled', 'type'],
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

function mountPage(withHeader = false) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  const component = withHeader
    ? defineComponent({
        components: { Notifications, NavNotificationPopover },
        template: '<div><NavNotificationPopover /><Notifications /></div>',
      })
    : Notifications
  return mount(component, {
    global: {
      plugins: [i18n],
      stubs: {
        HflTablePanel: HflTablePanelStub,
        HflPagination: HflPaginationStub,
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElEmpty: ElEmptyStub,
        ElButton: ElButtonStub,
        HflPopover: defineComponent({ template: '<div><slot name="reference" /><slot /></div>' }),
      },
      directives: { loading: () => undefined },
    },
  })
}

describe('Notifications page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    localStorage.clear()
    currentUser.value = { id: 1, email: 'test@example.com', username: 'test', access_profile: { org_key: 'test-org' } } as typeof currentUser.value
    mocks.list.mockResolvedValue({
      count: 1,
      unread_count: 1,
      results: [notification('1')],
    })
    mocks.markAll.mockResolvedValue(undefined)
    mocks.markRead.mockResolvedValue(undefined)
    mocks.push.mockResolvedValue(undefined)
  })

  it('marks all notifications read without acknowledging alerts', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const markAllButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Mark All Read')
    ))
    expect(markAllButton).toBeDefined()
    const primaryAction = wrapper.findAllComponents(ElButtonStub).find(button => (
      button.text().includes('Mark All Read')
    ))
    expect(primaryAction!.props('type')).toBe('primary')
    await markAllButton!.trigger('click')
    await flushPromises()

    expect(mocks.markAll).toHaveBeenCalledOnce()
    expect(mocks.markRead).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('renders only the three agreed columns and keeps the result icon inside the notification', async () => {
    mocks.list.mockResolvedValue({
      count: 2, unread_count: 1,
      results: [
        {
          ...notification('1'), kind: 'quota_increase_request', title: 'Quota increase rejected',
          summary: 'max_protected_sources: request for 103 was rejected.', severity: 'warning',
        },
        { ...notification('2', true), severity: 'info' },
      ],
    })
    const wrapper = mountPage()
    await flushPromises()
    const columns = wrapper.findAll('.notification-column')
    expect(columns.map(column => column.attributes('data-label'))).toEqual([
      'Notification', 'Read Status', 'Received At',
    ])
    expect(columns[0]!.findAll('.notification-outcome-icon')).toHaveLength(2)
    expect(columns[0]!.find('.notification-outcome-icon--rejected').exists()).toBe(true)
    expect(columns[0]!.find('.notifications-page__subject').text()).toBe('Quota Increase Rejected')
    expect(columns[0]!.text()).not.toContain('max_protected_sources')
    expect(wrapper.findAll('.notifications-page__link.is-unread')).toHaveLength(1)
    expect(columns[1]!.findAll('.notifications-page__read-status').map(status => status.text())).toEqual(['Unread', 'Read'])
    expect(columns[1]!.findAll('.notifications-page__read-status.is-unread')).toHaveLength(1)
    const rowClass = wrapper.getComponent(ElTableStub).props('rowClassName')
    expect(rowClass({ row: notification('1') })).toBe('notifications-page__row--unread')
    expect(rowClass({ row: notification('2', true) })).toBe('')
    await columns[0]!.get('.notifications-page__link').trigger('click')
    await flushPromises()
    expect(mocks.markRead).toHaveBeenCalledWith('1', 'test-org')
    expect(mocks.push).toHaveBeenCalledWith('/ops/alerts')
    expect(wrapper.find('.notifications-page__link.is-unread').exists()).toBe(false)
    wrapper.unmount()
  })

  it('disables Mark All Read when all notifications are read', async () => {
    mocks.list.mockResolvedValue({ count: 1, unread_count: 0, results: [notification('1', true)] })
    const wrapper = mountPage()
    await flushPromises()
    const button = wrapper.findAll('button').find(button => button.text().includes('Mark All Read'))!
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(mocks.markAll).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each(['all', 'single', 'off-page'])('reloads the requested page after an overlapping %s read event', async (kind) => {
    let resolveOlder!: (value: unknown) => void
    const pageOne = { count: 40, unread_count: 40, results: [notification('page-one')] }
    const pageTwo = { count: 40, unread_count: 0, results: [notification('page-two', true)] }
    mocks.list.mockResolvedValueOnce(pageOne)
      .mockReturnValueOnce(new Promise(resolve => { resolveOlder = resolve }))
      .mockResolvedValueOnce(pageTwo)
    const wrapper = mountPage()
    try {
      await flushPromises()
      await wrapper.get('[data-test="pagination"]').trigger('click')
      expect(mocks.list).toHaveBeenLastCalledWith(20, 2, 'test-org')
      publishNotificationReadChange(kind === 'all'
        ? { scope: notificationScope(), all: true }
        : { scope: notificationScope(), id: kind === 'off-page' ? 'another-page' : 'page-one' })
      await flushPromises()
      expect(wrapper.get('[data-test="pagination"]').text()).toBe('Page 2')
      expect(wrapper.get('.notification-row').text()).toContain('page-two')
      expect(mocks.list).toHaveBeenCalledTimes(3)
      resolveOlder({ count: 40, unread_count: 40, results: [notification('stale-page-two')] })
      await flushPromises()
      expect(wrapper.get('.notification-row').text()).toContain('page-two')
      expect(wrapper.text()).not.toContain('stale-page-two')
    } finally {
      wrapper.unmount()
    }
  })

  it('ignores read events for another user or organization', async () => {
    const wrapper = mountPage()
    try {
      await flushPromises()
      const scope = notificationScope()
      publishNotificationReadChange({ scope: { ...scope, organizationKey: 'other-org' }, all: true })
      publishNotificationReadChange({ scope: { ...scope, userId: 2 }, all: true })
      await flushPromises()
      expect(wrapper.get('.notifications-page__read-status').text()).toBe('Unread')
      expect(mocks.list).toHaveBeenCalledOnce()
    } finally {
      wrapper.unmount()
    }
  })

  it('does not apply a header mark-all completion to the newly selected organization', async () => {
    let resolveRead!: () => void
    mocks.markAll.mockReturnValueOnce(new Promise<void>(resolve => { resolveRead = resolve }))
    mocks.list.mockImplementation(async () => ({
      count: 1, unread_count: 1,
      results: [notification(currentUser.value?.access_profile?.org_key || '')],
    }))
    const wrapper = mountPage(true)
    try {
      await flushPromises()
      await wrapper.get('.nn-mark-all').trigger('click')
      currentUser.value = { ...currentUser.value!, access_profile: { ...currentUser.value!.access_profile!, org_key: 'new-org' } }
      await flushPromises()
      expect(wrapper.get('.notifications-page__subject').text()).toBe('Notification new-org')
      resolveRead()
      await flushPromises()
      expect(wrapper.get('.notifications-page__read-status').text()).toBe('Unread')
      expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    } finally {
      wrapper.unmount()
    }
  })

  it('preserves the current page when session refresh returns the same account and organization', async () => {
    const wrapper = mountPage()
    try {
      await flushPromises()
      await wrapper.get('[data-test="pagination"]').trigger('click')
      await flushPromises()
      mocks.list.mockClear()
      currentUser.value = { ...currentUser.value!, access_profile: { ...currentUser.value!.access_profile! } }
      await flushPromises()
      expect(wrapper.get('[data-test="pagination"]').text()).toBe('Page 2')
      expect(mocks.list).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('synchronizes the mounted header and inbox immediately in both directions', async () => {
    mocks.list.mockImplementation(async () => ({
      count: 1, unread_count: 1, results: [notification('1')],
    }))
    const wrapper = mountPage(true)
    await flushPromises()
    expect(wrapper.get('.nav-notification-badge').text()).toBe('1')
    await wrapper.get('.notifications-page').findAll('button').find(button => button.text().includes('Mark All Read'))!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.nav-notification-badge').exists()).toBe(false)
    expect(wrapper.find('.notification-row').text()).toContain('Read')
    expect(mocks.list).toHaveBeenCalledTimes(2)
    wrapper.unmount()

    const inbox = mountPage(true)
    await flushPromises()
    await inbox.get('.nn-item').trigger('click')
    await flushPromises()
    expect(inbox.find('.notification-row').text()).toContain('Read')
    expect(inbox.find('.nav-notification-badge').exists()).toBe(false)
    expect(mocks.markRead).toHaveBeenCalledWith('1', 'test-org')
    inbox.unmount()
  })

  it('preserves existing rows and shows an error after refresh fails', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(mocks.list).toHaveBeenCalledWith(20, 1, 'test-org')
    mocks.list.mockRejectedValue(new Error('network unavailable'))

    const refreshButton = wrapper.findAll('button').find((button) => (
      button.attributes('title') === en.ops.task.btnRefresh
    ))
    expect(refreshButton).toBeDefined()
    await refreshButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Notification 1')
    expect(wrapper.get('.notifications-page__error').text()).toContain(
      'Failed to load notifications',
    )
    wrapper.unmount()
  })
})
