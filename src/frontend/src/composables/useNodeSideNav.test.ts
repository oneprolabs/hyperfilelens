// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { useNodeSideNav } from './useNodeSideNav'

function mountMenus() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: {
      en: {
        assetsPage: {
          side: {
            groupGovernance: 'Organization',
          },
        },
        settings: {
          nav: {
            organizationHub: 'Organization',
            members: 'Members',
            subscription: 'Subscription',
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
  it('exposes organization configuration pages without system settings', () => {
    expect(mountMenus().value.map((group) => group.label)).toEqual([
      'Organization',
    ])
  })
})
