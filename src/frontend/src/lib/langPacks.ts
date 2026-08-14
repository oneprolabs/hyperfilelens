import { shallowRef } from 'vue'
import { i18n, registerLocale, unregisterLocale } from '../i18n'

export type InstalledLangPack = {
  schema?: number
  id: string
  display_name: string
  frontend_code: string
  backend_code: string
  component_locale?: string
  element_plus_locale?: string
  aliases?: string[]
  version: string
  component_messages?: Record<string, unknown>
}

export const installedLangPacks = shallowRef<InstalledLangPack[]>([])

export function hasMultipleLocales(): boolean {
  return Object.keys(i18n.global.messages.value).length > 1
}

function isInstalledLangPack(value: unknown): value is InstalledLangPack {
  if (!value || typeof value !== 'object') return false
  const pack = value as Record<string, unknown>
  return (
    typeof pack.id === 'string' &&
    /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(pack.id) &&
    typeof pack.display_name === 'string' &&
    typeof pack.frontend_code === 'string' &&
    pack.frontend_code === pack.frontend_code.toLowerCase() &&
    pack.frontend_code !== 'en' &&
    /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(pack.frontend_code) &&
    typeof pack.backend_code === 'string' &&
    pack.backend_code === pack.backend_code.toLowerCase() &&
    pack.backend_code !== 'en' &&
    /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(pack.backend_code) &&
    typeof pack.version === 'string' &&
    (pack.schema === undefined || pack.schema === 1 || pack.schema === 2) &&
    (pack.component_locale === undefined ||
      (typeof pack.component_locale === 'string' &&
        /^[A-Za-z0-9_-]+$/.test(pack.component_locale))) &&
    (pack.element_plus_locale === undefined ||
      (typeof pack.element_plus_locale === 'string' &&
        /^[A-Za-z0-9_-]+$/.test(pack.element_plus_locale))) &&
    (pack.aliases === undefined ||
      (Array.isArray(pack.aliases) && pack.aliases.every(
        (alias) => typeof alias === 'string' &&
          alias === alias.toLowerCase() &&
          alias !== 'en' &&
          /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/.test(alias),
      )))
  )
}

function isMessageCatalog(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export async function loadInstalledLangPacks(): Promise<void> {
  for (const pack of installedLangPacks.value) {
    unregisterLocale(pack.frontend_code)
  }
  installedLangPacks.value = []
  try {
    const res = await fetch('/locales/installed.json', { cache: 'no-cache' })
    if (!res.ok) return
    const data = (await res.json()) as { app_version?: unknown, packs?: InstalledLangPack[] }
    const appVersion = typeof data.app_version === 'string' ? data.app_version : ''
    const packs = (data.packs ?? []).filter(
      (pack) => isInstalledLangPack(pack) &&
        (pack.schema !== 2 || (Boolean(appVersion) && pack.version === appVersion)),
    )
    const loadedPacks: InstalledLangPack[] = []
    for (const pack of packs) {
      try {
        const messagesResponse = await fetch(
          `/locales/${encodeURIComponent(pack.id)}/frontend/messages.json`,
          { cache: 'no-cache' },
        )
        if (!messagesResponse.ok) continue
        const messages: unknown = await messagesResponse.json()
        if (!isMessageCatalog(messages)) continue
        let componentMessages: Record<string, unknown> | undefined
        if (pack.schema === 2 || pack.component_locale) {
          const componentResponse = await fetch(
            `/locales/${encodeURIComponent(pack.id)}/frontend/element-plus.json`,
            { cache: 'no-cache' },
          )
          if (!componentResponse.ok) continue
          const componentData: unknown = await componentResponse.json()
          if (!isMessageCatalog(componentData)) continue
          componentMessages = componentData
        } else if (pack.element_plus_locale) {
          const componentData: unknown = await import(
            /* @vite-ignore */ `element-plus/dist/locale/${pack.element_plus_locale}.mjs`
          ).then((module) => module.default)
          if (!isMessageCatalog(componentData)) continue
          componentMessages = componentData
        }
        registerLocale(
          pack.frontend_code,
          messages,
          [...new Set([pack.backend_code, ...(pack.aliases ?? [])])],
        )
        loadedPacks.push({ ...pack, component_messages: componentMessages })
      } catch {
        // One malformed optional pack must not prevent other valid packs loading.
      }
    }
    installedLangPacks.value = loadedPacks
  } catch {
    installedLangPacks.value = []
  }
}
