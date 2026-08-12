import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  listUserNotifications,
  markAllUserNotificationsRead,
  markUserNotificationRead,
} from './notificationApi'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
}))

vi.mock('./api', () => ({ api: mocks.api }))

describe('notificationApi inbox routes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.mockResolvedValue({ count: 0, unread_count: 0, results: [] })
  })

  it('uses the canonical plural API prefix', async () => {
    await listUserNotifications(20, 2)
    await markUserNotificationRead('17')
    await markAllUserNotificationsRead()

    expect(mocks.api).toHaveBeenNthCalledWith(
      1,
      '/api/v1/notifications/inbox/?page=2&page_size=20',
    )
    expect(mocks.api).toHaveBeenNthCalledWith(
      2,
      '/api/v1/notifications/inbox/17/read/',
      { method: 'POST', body: '{}' },
    )
    expect(mocks.api).toHaveBeenNthCalledWith(
      3,
      '/api/v1/notifications/inbox/mark-all-read/',
      { method: 'POST', body: '{}' },
    )
  })
})
