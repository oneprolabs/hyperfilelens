// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { useAccountCenterMenus } from './useAccountCenterMenus'

function mountMenus() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: {
      en: {
        account: {
          groupAccountCenter: 'Account Center',
          sidebarProfile: 'Personal Settings',
          pageProfileTitle: 'Personal Settings',
          sidebarNotifications: 'Notifications',
          pageNotificationsTitle: 'Notifications',
        },
      },
    },
  })

  let menus: ReturnType<typeof useAccountCenterMenus> | undefined
  mount(
    defineComponent({
      setup() {
        menus = useAccountCenterMenus()
        return () => null
      },
    }),
    { global: { plugins: [i18n] } },
  )
  return menus!
}

describe('useAccountCenterMenus', () => {
  it('keeps personal settings and notifications together in Account Center', () => {
    const menus = mountMenus()

    expect(menus.value[0].label).toBe('Account Center')
    expect(menus.value[0].children?.map((item) => ({
      label: item.label,
      pageTitle: item.pageTitle,
      to: item.to,
    }))).toEqual([
      {
        label: 'Personal Settings',
        pageTitle: 'Personal Settings',
        to: '/account/profile',
      },
      {
        label: 'Notifications',
        pageTitle: 'Notifications',
        to: '/account/notifications',
      },
    ])
  })
})
