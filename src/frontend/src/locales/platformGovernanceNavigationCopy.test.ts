import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from './en'

function readPack(lang: string, file: string) {
  return JSON.parse(readFileSync(resolve(
    process.cwd(), `../../language-packs/packs/${lang}/frontend/${file}`,
  ), 'utf8'))
}

describe('Admin Console governance navigation copy', () => {
  it('uses Name consistently for tenant directory columns without changing the member detail label', () => {
    expect([
      en.settings.membersPage.name, en.settings.groupsPage.name,
      en.settings.rolesPage.name, en.settings.resourcesPage.name,
    ]).toEqual(['Name', 'Name', 'Name', 'Name'])
    expect(en.settings.membersPage.member).toBe('Member')
  })

  it.each(['zh-hans', 'es'])('keeps the %s member Name column copy and source lock current', (lang) => {
    const messages = readPack(lang, 'messages.json')
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    expect(messages['settings.membersPage.name']).toBe(messages.settings.groupsPage.name)
    expect(lock['settings.membersPage.name']).toBe(
      createHash('sha256').update(JSON.stringify(en.settings.membersPage.name)).digest('hex'),
    )
  })

  it('uses Title Case for the shared save action', () => {
    expect(en.common.saveChanges).toBe('Save Changes')
  })

  it.each(['zh-hans', 'es'])('keeps the %s save action copy and source lock current', (lang) => {
    const messages = readPack(lang, 'messages.json') as typeof en
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    expect(messages.common.saveChanges.trim()).not.toBe('')
    expect(lock['common.saveChanges']).toBe(
      createHash('sha256').update(JSON.stringify(en.common.saveChanges)).digest('hex'),
    )
  })

  it('uses account creation and organization affiliations rather than Joined or Account Type in user headers', () => {
    expect([
      en.platformOps.users.colName, en.platformOps.users.colOrgName,
      en.platformOps.users.colJoined,
    ]).toEqual(['Name', 'Organizations', 'Created'])
  })

  it.each(['zh-hans', 'es'])('keeps the %s user-directory column copy and source locks current', (lang) => {
    const messages = readPack(lang, 'messages.json') as typeof en
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    for (const key of ['colName', 'colOrgName', 'colJoined'] as const) {
      expect(messages.platformOps.users[key].trim()).not.toBe('')
      expect(lock[`platformOps.users.${key}`]).toBe(
        createHash('sha256').update(JSON.stringify(en.platformOps.users[key])).digest('hex'),
      )
    }
  })

  it.each(['zh-hans', 'es'])('has current %s copy-failure feedback and source lock', (lang) => {
    const messages = readPack(lang, 'messages.json') as typeof en
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    expect(messages.common.copyFailed.trim()).not.toBe('')
    expect(lock['common.copyFailed']).toBe(
      createHash('sha256').update(JSON.stringify(en.common.copyFailed)).digest('hex'),
    )
  })

  it('uses the same Enable and Disable terminology for platform users', () => {
    expect([
      en.platformOps.users.activate, en.platformOps.users.deactivate,
      en.platformOps.users.deactivateConfirmTitle,
    ]).toEqual(['Enable', 'Disable', 'Disable User'])
    expect(en.platformOps.users.resetPasswordHint).toContain('{email}')
    expect(en.platformOps.users.resetPasswordHint).toContain('external identity providers')
  })

  it.each(['zh-hans', 'es'])('keeps %s user-action translations and source locks current', (lang) => {
    const messages = readPack(lang, 'messages.json') as typeof en
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    for (const key of [
      'activate', 'deactivate', 'activateSuccess', 'deactivateSuccess',
      'deactivateConfirmTitle', 'deactivateConfirmMessage', 'resetPasswordHint',
    ] as const) {
      expect(messages.platformOps.users[key].trim()).not.toBe('')
      expect(lock[`platformOps.users.${key}`]).toBe(
        createHash('sha256').update(JSON.stringify(en.platformOps.users[key])).digest('hex'),
      )
    }
    expect(messages.platformOps.users.resetPasswordHint).toContain('{email}')
  })

  it('uses Enable and Disable consistently for organization actions and confirmation', () => {
    expect([
      en.platformOps.orgs.activate, en.platformOps.orgs.deactivate,
      en.platformOps.orgs.deactivateConfirmTitle, en.platformOps.orgs.deactivateConfirmAction,
    ]).toEqual(['Enable', 'Disable', 'Disable Organization', 'Disable'])
  })

  it.each(['zh-hans', 'es'])('keeps %s organization action translations and source locks current', (lang) => {
    const messages = readPack(lang, 'messages.json') as typeof en
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    for (const key of [
      'activate', 'deactivate', 'deactivateConfirmTitle',
      'deactivateConfirmMessage', 'deactivateConfirmAction',
    ] as const) {
      expect(messages.platformOps.orgs[key].trim()).not.toBe('')
      expect(messages.platformOps.orgs[key]).not.toBe(en.platformOps.orgs[key])
      expect(lock[`platformOps.orgs.${key}`]).toBe(
        createHash('sha256').update(JSON.stringify(en.platformOps.orgs[key])).digest('hex'),
      )
    }
  })

  it('distinguishes platform roles without renaming the other entries or tenant roles', () => {
    const nav = en.platformOps.nav
    expect([
      nav.orgs, nav.users, nav.roles, nav.resources,
      nav.platformQuotaUsage, nav.platformInstanceLicense,
    ]).toEqual([
      'Organizations', 'Users', 'Platform Roles', 'Resources', 'Subscriptions', 'License',
    ])
    expect(en.settings.nav.roles).toBe('Roles')
  })

  it.each(['zh-hans', 'es'])('updates the %s label and its English source lock', (lang) => {
    const messages = readPack(lang, 'messages.json') as typeof en
    const lock = readPack(lang, 'source-lock.json') as Record<string, string>
    const label = messages.platformOps.nav.roles
    expect(label).toBeTypeOf('string')
    expect(label.trim()).not.toBe('')
    expect(label).toBe(label.trim())
    expect(label).not.toBe(en.platformOps.nav.roles)
    expect(label).not.toBe(messages.settings.nav.roles)
    expect(lock['platformOps.nav.roles']).toBe(
      createHash('sha256').update(JSON.stringify(en.platformOps.nav.roles)).digest('hex'),
    )
  })
})
