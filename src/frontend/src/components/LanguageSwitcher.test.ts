// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  DEFAULT_LOCALE,
  i18n,
  registerLocale,
  selectLocale,
  unregisterLocale,
} from '../i18n'
import { installedLangPacks } from '../lib/langPacks'
import LanguageSwitcher from './LanguageSwitcher.vue'

afterEach(() => {
  selectLocale(DEFAULT_LOCALE)
  unregisterLocale('zh-hans')
  installedLangPacks.value = []
  document.body.innerHTML = ''
})

describe('LanguageSwitcher', () => {
  it('keeps hover preview visually distinct from the selected language', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/styles/nav-dropdown-panel.css'), 'utf8')
    expect(source).toMatch(/\.hfl-language-switcher-popper \.el-dropdown-menu\s*{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*gap:\s*2px;/s)
    expect(source).toMatch(/\.hfl-language-switcher-popper \.el-dropdown-menu__item:not\(\.is-selected\):hover[\s\S]*?var\(--color-primary-light/)
    expect(source).toMatch(/\.hfl-language-switcher-popper \.el-dropdown-menu__item\.is-selected\s*,[\s\S]*?font-weight:\s*600/)
    expect(source).toMatch(/:root\[data-theme="dark"\][\s\S]*?\.hfl-language-switcher-popper[\s\S]*?background:\s*var\(--color-primary-light/)
    expect(source).toMatch(/\.hfl-language-switcher-popper--auth\.el-popper[\s\S]*?background:\s*linear-gradient/)
    expect(source).toMatch(/\.hfl-language-switcher-popper--auth \.el-dropdown-menu__item:not\(\.is-selected\):hover[\s\S]*?background:\s*rgba\(109, 94, 246, 0\.14\)/)
    expect(source).toMatch(/\.hfl-language-switcher-popper--auth \.el-dropdown-menu__item\.is-selected[\s\S]*?background:\s*rgba\(109, 94, 246, 0\.2\)/)
  })

  it('shows user-facing language names and selects a language directly', async () => {
    registerLocale('zh-hans', { nav: { languageLabel: 'Language' } }, ['zh', 'zh-cn'])
    installedLangPacks.value = [{
      id: 'zh-hans',
      display_name: 'Simplified Chinese',
      frontend_code: 'zh-hans',
      backend_code: 'zh-hans',
      aliases: ['zh', 'zh-cn'],
      version: '0.2.0',
    }]

    const wrapper = mount(LanguageSwitcher, {
      attachTo: document.body,
      props: { variant: 'auth' },
      global: { plugins: [i18n, ElementPlus] },
    })

    const trigger = wrapper.get('button')
    expect(trigger.text()).toContain('English')
    expect(trigger.text()).not.toContain('ZH-HANS')

    await trigger.trigger('click')
    await flushPromises()
    const items = [...document.body.querySelectorAll<HTMLElement>('.el-dropdown-menu__item')]
    expect(items.map((item) => item.textContent?.trim())).toEqual(['English', 'Simplified Chinese'])
    expect(items[0]?.getAttribute('aria-current')).toBe('true')
    expect(items[1]?.hasAttribute('aria-current')).toBe(false)

    items[1]?.click()
    await flushPromises()
    expect(i18n.global.locale.value).toBe('zh-hans')
    expect(trigger.text()).toContain('Simplified Chinese')
    expect(trigger.text()).not.toContain('ZH-HANS')
    expect(wrapper.emitted('change')).toEqual([['zh-hans']])
    expect(items[0]?.hasAttribute('aria-current')).toBe(false)
    expect(items[1]?.getAttribute('aria-current')).toBe('true')

    await trigger.trigger('click')
    await flushPromises()
    const selectedItem = [...document.body.querySelectorAll<HTMLElement>('.el-dropdown-menu__item')]
      .find((item) => item.textContent?.includes('Simplified Chinese'))
    selectedItem?.click()
    await flushPromises()
    expect(wrapper.emitted('change')).toEqual([['zh-hans']])

    wrapper.unmount()
  })
})
