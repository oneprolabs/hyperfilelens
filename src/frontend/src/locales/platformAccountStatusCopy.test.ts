// @vitest-environment jsdom
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import PlatformOpsActiveTag from '../platform-ops/components/PlatformOpsActiveTag.vue'
import { en } from './en'

function readPackFile(lang: string, file: string) {
  return JSON.parse(readFileSync(resolve(
    process.cwd(), `../../language-packs/packs/${lang}/frontend/${file}`,
  ), 'utf8'))
}

const catalogs = [
  { lang: 'en', messages: en },
  { lang: 'zh-hans', messages: readPackFile('zh-hans', 'messages.json') as typeof en },
  { lang: 'es', messages: readPackFile('es', 'messages.json') as typeof en },
]

describe.each(catalogs)('Platform account status copy ($lang)', ({ lang, messages }) => {
  const enabled = messages.platformOps.users.active
  const disabled = messages.platformOps.users.inactive

  it('aligns user and organization statistics with filters and row status labels', () => {
    const { users, orgs } = messages.platformOps
    expect(enabled).not.toBe(disabled)
    if (lang === 'en') {
      expect(enabled).toBe('Enabled')
      expect(disabled).toBe('Disabled')
    }
    // Keep existing translation keys for older frontend bundles and callers.
    expect(users.statActive).toBe(enabled)
    expect(users.statInactive).toBe(disabled)
    expect(orgs.statActive).toBe(enabled)
    expect(orgs.statInactive).toBe(disabled)
  })

  it('keeps Never Signed In separate from the enabled/disabled distinction', () => {
    const { users } = messages.platformOps
    expect(users.statNeverSignedIn).toBe(users.neverSignedIn)
    expect([enabled, disabled]).not.toContain(users.neverSignedIn)
  })

  it.each([true, false])('renders the matching status label when is_active=%s', (active) => {
    const wrapper = mount(PlatformOpsActiveTag, {
      props: { active },
      global: {
        plugins: [createI18n({ legacy: false, locale: lang, messages: { [lang]: messages } })],
        stubs: { ElTag: { template: '<span><slot /></span>' } },
      },
    })
    expect(wrapper.text()).toBe(active ? enabled : disabled)
    wrapper.unmount()
  })
})

describe('Platform account status language-pack source locks', () => {
  it.each(['zh-hans', 'es'])('matches the canonical English copy in %s', (lang) => {
    const lock = readPackFile(lang, 'source-lock.json') as Record<string, string>
    for (const section of ['users', 'orgs'] as const) {
      for (const key of ['statActive', 'statInactive'] as const) {
        const digest = createHash('sha256')
          .update(JSON.stringify(en.platformOps[section][key])).digest('hex')
        expect(lock[`platformOps.${section}.${key}`]).toBe(digest)
      }
    }
  })
})
