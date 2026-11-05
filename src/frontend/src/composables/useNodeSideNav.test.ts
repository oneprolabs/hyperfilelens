// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import { useNodeSideNav } from './useNodeSideNav'
import { en } from '../locales/en'

const mocks = vi.hoisted(() => ({ allowed: true }))
vi.mock('./useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: () => mocks.allowed }),
}))

function mountMenus() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: {
      en: {
        account: en.account,
        assetsPage: {
          side: {
            groupGovernance: en.assetsPage.side.groupGovernance,
          },
        },
        settings: {
          nav: {
            organizationHub: 'Organization',
            subscription: 'Usage',
          },
        },
      },
    },
  })

  let menus: ReturnType<typeof useNodeSideNav> | undefined
  mount(
    defineComponent({
      setup() {
        menus = useNodeSideNav()
        return () => null
      },
    }),
    { global: { plugins: [i18n] } },
  )
  return menus!
}

describe('useNodeSideNav', () => {
  it('exposes the Community governance page without enterprise settings', () => {
    expect(mountMenus().value.map((group) => group.label)).toEqual([
      'Organization Management',
      'Account',
    ])
    expect(mountMenus().value[0]?.children?.map((item) => item.label)).toEqual([
      'Usage',
    ])
  })

  it('keeps Account accessible without any organization permissions', () => {
    mocks.allowed = false
    try {
      const menus = mountMenus().value
      expect(menus.map(group => group.label)).toEqual(['Account'])
      expect(menus[0].children?.map(item => item.to)).toEqual([
        '/account/profile', '/account/notifications',
      ])
    } finally {
      mocks.allowed = true
    }
  })
})
