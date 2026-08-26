// @vitest-environment jsdom

import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { currentUser } from './useAuth'
import {
  DEFAULT_LOCALE,
  i18n,
  registerLocale,
  selectLocale,
  setAuthenticatedLocalePreference,
  unregisterLocale,
} from '../i18n'
import { installedLangPacks } from '../lib/langPacks'
import { useLocaleSwitch } from './useLocaleSwitch'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  notifyError: vi.fn(),
}))

vi.mock('../lib/api', () => ({ api: mocks.api }))
vi.mock('../lib/notify', () => ({ notifyError: mocks.notifyError }))

afterEach(() => {
  currentUser.value = null
  setAuthenticatedLocalePreference(null)
  selectLocale(DEFAULT_LOCALE)
  unregisterLocale('zh-hans')
  unregisterLocale('pt-br')
  installedLangPacks.value = []
  mocks.api.mockReset()
  mocks.notifyError.mockReset()
})

describe('useLocaleSwitch', () => {
  it('persists rapid language changes in user-selection order', async () => {
    registerLocale('zh-hans', { nav: { overview: 'Translated overview' } }, ['zh', 'zh-cn'])
    installedLangPacks.value = [{
      id: 'zh-hans',
      display_name: 'Simplified Chinese',
      frontend_code: 'zh-hans',
      backend_code: 'zh-hans',
      aliases: ['zh', 'zh-cn'],
      version: '0.2.0',
    }]
    currentUser.value = {
      id: 7,
      email: 'locale@example.com',
      username: 'locale-user',
      language: 'en',
    }

    const writes: Array<() => void> = []
    mocks.api.mockImplementation(() => new Promise<void>((resolve) => writes.push(resolve)))
    let localeSwitch: ReturnType<typeof useLocaleSwitch> | undefined
    const wrapper = mount(defineComponent({
      setup() {
        localeSwitch = useLocaleSwitch()
        return () => h('div')
      },
    }), { global: { plugins: [i18n] } })

    localeSwitch?.selectLocale('zh-hans')
    await vi.waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(1))
    localeSwitch?.selectLocale('en')
    await Promise.resolve()
    expect(mocks.api).toHaveBeenCalledTimes(1)

    writes[0]?.()
    await vi.waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(2))
    expect(mocks.api.mock.calls.map((call) => JSON.parse(call[1].body).language)).toEqual([
      'zh-hans',
      'en',
    ])
    writes[1]?.()
    await Promise.resolve()
    wrapper.unmount()
  })

  it('drops a queued preference write after the authenticated user changes', async () => {
    registerLocale('zh-hans', { nav: { overview: 'Translated overview' } }, ['zh', 'zh-cn'])
    installedLangPacks.value = [{
      id: 'zh-hans',
      display_name: 'Simplified Chinese',
      frontend_code: 'zh-hans',
      backend_code: 'zh-hans',
      aliases: ['zh', 'zh-cn'],
      version: '0.2.0',
    }]
    currentUser.value = {
      id: 9,
      email: 'first-locale-user@example.com',
      username: 'first-locale-user',
      language: 'en',
    }

    let finishFirstWrite: (() => void) | undefined
    mocks.api.mockImplementation(() => new Promise<void>((resolve) => {
      finishFirstWrite = resolve
    }))
    let localeSwitch: ReturnType<typeof useLocaleSwitch> | undefined
    const wrapper = mount(defineComponent({
      setup() {
        localeSwitch = useLocaleSwitch()
        return () => h('div')
      },
    }), { global: { plugins: [i18n] } })

    localeSwitch?.selectLocale('zh-hans')
    await vi.waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(1))
    localeSwitch?.selectLocale('en')
    currentUser.value = {
      id: 10,
      email: 'second-locale-user@example.com',
      username: 'second-locale-user',
      language: 'zh-hans',
    }

    finishFirstWrite?.()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(currentUser.value?.id).toBe(10)
    expect(currentUser.value?.language).toBe('zh-hans')
    wrapper.unmount()
  })

  it('stores the backend language identity when frontend and backend codes differ', async () => {
    registerLocale('pt-br', { nav: { overview: 'Translated overview' } }, ['pt'])
    installedLangPacks.value = [{
      id: 'pt-br',
      display_name: 'Portuguese',
      frontend_code: 'pt-br',
      backend_code: 'pt',
      aliases: ['pt'],
      version: '0.2.0',
    }]
    currentUser.value = {
      id: 8,
      email: 'locale-mapping@example.com',
      username: 'locale-mapping',
      language: 'en',
    }
    mocks.api.mockResolvedValue(undefined)

    let localeSwitch: ReturnType<typeof useLocaleSwitch> | undefined
    const wrapper = mount(defineComponent({
      setup() {
        localeSwitch = useLocaleSwitch()
        return () => h('div')
      },
    }), { global: { plugins: [i18n] } })

    localeSwitch?.selectLocale('pt-br')

    await vi.waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(1))
    expect(i18n.global.locale.value).toBe('pt-br')
    expect(currentUser.value?.language).toBe('pt')
    expect(JSON.parse(mocks.api.mock.calls[0]?.[1].body).language).toBe('pt')
    wrapper.unmount()
  })

  it('syncs an explicit pre-login selection even when the active locale already matches', async () => {
    registerLocale('zh-hans', { nav: { overview: 'Translated overview' } }, ['zh', 'zh-cn'])
    installedLangPacks.value = [{
      id: 'zh-hans',
      display_name: 'Simplified Chinese',
      frontend_code: 'zh-hans',
      backend_code: 'zh-hans',
      aliases: ['zh', 'zh-cn'],
      version: '0.2.0',
    }]
    selectLocale('zh-hans')
    currentUser.value = {
      id: 11,
      email: 'pre-login-locale@example.com',
      username: 'pre-login-locale',
      language: 'en',
    }
    mocks.api.mockResolvedValue(undefined)

    let localeSwitch: ReturnType<typeof useLocaleSwitch> | undefined
    const wrapper = mount(defineComponent({
      setup() {
        localeSwitch = useLocaleSwitch()
        return () => h('div')
      },
    }), { global: { plugins: [i18n] } })

    expect(localeSwitch?.syncAuthenticatedLocale('zh-hans')).toBe(true)

    await vi.waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(1))
    expect(i18n.global.locale.value).toBe('zh-hans')
    expect(currentUser.value?.language).toBe('zh-hans')
    expect(JSON.parse(mocks.api.mock.calls[0]?.[1].body).language).toBe('zh-hans')
    wrapper.unmount()
  })

  it('keeps the explicit locale active when saving the profile preference fails', async () => {
    registerLocale('zh-hans', { nav: { overview: 'Translated overview' } }, ['zh', 'zh-cn'])
    installedLangPacks.value = [{
      id: 'zh-hans',
      display_name: 'Simplified Chinese',
      frontend_code: 'zh-hans',
      backend_code: 'zh-hans',
      aliases: ['zh', 'zh-cn'],
      version: '0.2.0',
    }]
    currentUser.value = {
      id: 12,
      email: 'locale-save-failure@example.com',
      username: 'locale-save-failure',
      language: 'en',
    }
    const saveError = new Error('save failed')
    mocks.api.mockRejectedValue(saveError)

    let localeSwitch: ReturnType<typeof useLocaleSwitch> | undefined
    const wrapper = mount(defineComponent({
      setup() {
        localeSwitch = useLocaleSwitch()
        return () => h('div')
      },
    }), { global: { plugins: [i18n] } })

    expect(localeSwitch?.syncAuthenticatedLocale('zh-hans')).toBe(true)

    await vi.waitFor(() => expect(mocks.notifyError).toHaveBeenCalledTimes(1))
    expect(i18n.global.locale.value).toBe('zh-hans')
    expect(currentUser.value?.language).toBe('zh-hans')
    expect(mocks.notifyError).toHaveBeenCalledWith(expect.objectContaining({
      error: saveError,
      dedupeKey: 'language-preference-save-failed',
    }))
    wrapper.unmount()
  })
})
