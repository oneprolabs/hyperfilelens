// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  close: vi.fn(),
  confirm: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessageBox: {
    close: mocks.close,
    confirm: mocks.confirm,
  },
}))

import { confirmSignOut } from './logout'

describe('confirmSignOut', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.confirm.mockResolvedValue('confirm')
  })

  it('opens without auto-focusing the confirm action', async () => {
    const t = (key: string) => key

    await expect(confirmSignOut(t)).resolves.toBe(true)

    expect(mocks.confirm).toHaveBeenCalledWith(
      'account.logoutConfirmBody',
      'account.logoutConfirmTitle',
      expect.objectContaining({
        autofocus: false,
        customClass: 'hfl-message-box--sign-out',
      }),
    )
  })
})
