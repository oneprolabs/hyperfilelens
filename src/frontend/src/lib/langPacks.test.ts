// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, normalizeStoredLocale, unregisterLocale } from '../i18n'
import { installedLangPacks, loadInstalledLangPacks } from './langPacks'

const zhPack = {
  schema: 2,
  id: 'zh-hans',
  display_name: 'Simplified Chinese',
  frontend_code: 'zh-hans',
  backend_code: 'zh-hans',
  component_locale: 'zh-cn',
  aliases: ['zh', 'zh-cn'],
  version: '0.2.0',
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('loadInstalledLangPacks', () => {
  beforeEach(() => {
    unregisterLocale('zh-hans')
    unregisterLocale('pt-br')
    unregisterLocale('es')
    installedLangPacks.value = []
    vi.restoreAllMocks()
  })

  it('loads schema 2 application and component catalogs atomically', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/locales/installed.json') {
        return jsonResponse({ app_version: '0.2.0', packs: [zhPack] })
      }
      if (url.endsWith('/frontend/messages.json')) return jsonResponse({ nav: { home: 'Home' } })
      if (url.endsWith('/frontend/element-plus.json')) return jsonResponse({ name: 'zh-cn' })
      return jsonResponse({}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toContain('zh-hans')
    expect(installedLangPacks.value).toHaveLength(1)
    expect(installedLangPacks.value[0]?.component_messages).toEqual({ name: 'zh-cn' })
    expect(normalizeStoredLocale('zh-Hant')).toBe('en')
  })

  it('maps the backend language identity to the frontend locale', async () => {
    const splitIdentityPack = {
      ...zhPack,
      frontend_code: 'pt-br',
      backend_code: 'pt',
      aliases: ['pt-br'],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/locales/installed.json') {
        return jsonResponse({ app_version: '0.2.0', packs: [splitIdentityPack] })
      }
      if (url.endsWith('/frontend/messages.json')) return jsonResponse({ nav: { home: 'Home' } })
      if (url.endsWith('/frontend/element-plus.json')) return jsonResponse({ name: 'pt-br' })
      return jsonResponse({}, 404)
    }))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toContain('pt-br')
    expect(normalizeStoredLocale('pt')).toBe('pt-br')
    unregisterLocale('pt-br')
  })

  it('maps unlisted regional tags to an installed primary language', async () => {
    const spanishPack = {
      ...zhPack,
      id: 'es',
      display_name: 'Español',
      frontend_code: 'es',
      backend_code: 'es',
      aliases: ['es-es', 'es-mx'],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/locales/installed.json') {
        return jsonResponse({ app_version: '0.2.0', packs: [spanishPack] })
      }
      if (url.endsWith('/frontend/messages.json')) return jsonResponse({ nav: { home: 'Inicio' } })
      if (url.endsWith('/frontend/element-plus.json')) return jsonResponse({ name: 'es' })
      return jsonResponse({}, 404)
    }))

    await loadInstalledLangPacks()

    expect(normalizeStoredLocale('es-VE')).toBe('es')
    expect(normalizeStoredLocale('es-EC')).toBe('es')
    expect(normalizeStoredLocale('es-UY')).toBe('es')
    expect(normalizeStoredLocale('es-419')).toBe('es')
  })

  it('does not register a schema 2 pack when its component catalog fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/locales/installed.json') {
        return jsonResponse({ app_version: '0.2.0', packs: [zhPack] })
      }
      if (url.endsWith('/frontend/messages.json')) return jsonResponse({ nav: { home: 'Home' } })
      return jsonResponse({}, 404)
    }))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).not.toContain('zh-hans')
    expect(installedLangPacks.value).toEqual([])
  })

  it('keeps schema 1 packs compatible without component messages', async () => {
    const schemaOnePack = { ...zhPack, schema: 1, component_locale: undefined }
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/locales/installed.json') {
        return jsonResponse({ app_version: '0.2.0', packs: [schemaOnePack] })
      }
      if (url.endsWith('/frontend/messages.json')) return jsonResponse({ nav: { home: 'Home' } })
      return jsonResponse({}, 404)
    }))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toContain('zh-hans')
    expect(installedLangPacks.value[0]?.component_messages).toBeUndefined()
  })

  it('keeps schema 1 component locale metadata compatible', async () => {
    const schemaOnePack = {
      ...zhPack,
      schema: 1,
      component_locale: undefined,
      element_plus_locale: 'zh-cn',
    }
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/locales/installed.json') {
        return jsonResponse({ app_version: '0.2.0', packs: [schemaOnePack] })
      }
      if (url.endsWith('/frontend/messages.json')) return jsonResponse({ nav: { home: 'Home' } })
      return jsonResponse({}, 404)
    }))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toContain('zh-hans')
    expect(installedLangPacks.value[0]?.component_messages).toBeTruthy()
  })

  it('reserves the built-in English locale and aliases', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      app_version: '0.2.0',
      packs: [{ ...zhPack, aliases: ['en'] }],
    })))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toEqual(['en'])
    expect(installedLangPacks.value).toEqual([])
  })

  it('rejects a schema 2 pack from another application version', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      app_version: '0.2.1',
      packs: [zhPack],
    })))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toEqual(['en'])
    expect(installedLangPacks.value).toEqual([])
  })

  it('ignores invalid manifest entries and keeps English available', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      app_version: '0.2.0',
      packs: [{ ...zhPack, id: '../zh-hans' }],
    })))

    await loadInstalledLangPacks()

    expect(i18n.global.availableLocales).toEqual(['en'])
    expect(installedLangPacks.value).toEqual([])
  })
})
