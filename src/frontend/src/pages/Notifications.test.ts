// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../locales/en'
import Notifications from './Notifications.vue'

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
      <slot name="toolbar-utility" />
      <slot name="table" :table-max-height="500" />
      <slot name="footer" />
    </div>
  `,
})

const HflPaginationStub = defineComponent({
  template: '<div data-test="pagination" />',
})

const ElTableStub = defineComponent({
  props: ['data'],
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

const ElTableColumnStub = defineComponent({ template: '<div />' })
const ElEmptyStub = defineComponent({ template: '<div />' })
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

function mountPage() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(Notifications, {
    global: {
      plugins: [i18n],
      stubs: {
        HflTablePanel: HflTablePanelStub,
        HflPagination: HflPaginationStub,
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElEmpty: ElEmptyStub,
        ElButton: ElButtonStub,
      },
      directives: { loading: () => undefined },
    },
  })
}

describe('Notifications page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
    await markAllButton!.trigger('click')
    await flushPromises()

    expect(mocks.markAll).toHaveBeenCalledOnce()
    expect(mocks.markRead).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('preserves existing rows and shows an error after refresh fails', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(mocks.list).toHaveBeenCalledWith(20, 1)
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
