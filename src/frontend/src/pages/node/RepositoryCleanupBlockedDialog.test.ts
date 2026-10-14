import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createI18n } from 'vue-i18n'
import { compileStyle, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'
import { repositoryCleanupMessage } from '../../lib/repositoryCleanupPresentation'
import { en } from '../../locales/en'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')
const zh = JSON.parse(readFileSync(resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'), 'utf8'))

describe('Repository cleanup blocked dialog', () => {
  it('compiles scroll-container selectors without a page scope attribute on the teleported dialog', () => {
    const { descriptor } = parse(page)
    const style = descriptor.styles.find(style => style.scoped)!
    const compiled = compileStyle({ source: style.content, filename: 'Repositories.vue', id: 'data-v-repository-test', scoped: true })
    expect(compiled.errors).toEqual([])
    expect(compiled.code).toContain('.repo-cleanup-blocked-dialog > .el-dialog__body {')
    expect(compiled.code).not.toContain('.repo-cleanup-blocked-dialog[data-v-repository-test]')
  })
  it('uses natural Chinese association and blocker copy', () => {
    const i18n = createI18n({ legacy: false, locale: 'zh-hans', messages: { en, 'zh-hans': zh } })
    const t = i18n.global.t
    expect(t('repositoriesPage.cleanupBlockedAssociations', { n: 1 })).toBe(zh.repositoriesPage.cleanupBlockedAssociations.replace('{n}', '1'))
    expect(repositoryCleanupMessage({ code: 'associated_sources', count: 1, detail: 'Repository has 1 associated backup source(s).' }, t))
      .toBe(zh.repositoriesPage.cleanupBlockedSources.replace('{n}', '1'))
    expect(repositoryCleanupMessage({ code: 'associated_snapshots', count: 2, detail: 'Repository has 2 snapshot record(s).' }, t))
      .toBe(zh.repositoriesPage.cleanupBlockedSnapshots.replace('{n}', '2'))
  })

  it('bounds body scrolling and preserves all loaded association names and partial-list disclosure', () => {
    expect(page).toContain(':global(.el-dialog.repo-cleanup-blocked-dialog)')
    expect(page).toContain('flex-direction: column')
    expect(page).toContain('max-height: calc(var(--app-viewport-height, 100dvh)')
    expect(page).toContain(':global(.repo-cleanup-blocked-dialog > .el-dialog__body)')
    expect(page).toContain('flex: 1 1 auto')
    expect(page).toContain('min-height: 0')
    expect(page).toContain('overflow-y: auto')
    expect(page).toContain('class="repo-cleanup-blocked-dialog"')
    expect(page).toContain('<RepositoryBlockedAssociations')
    expect(page).toContain('v-if="repositoryCleanupBlockedDialogOpen"')
    const summary = page.slice(page.indexOf('function showRepositoryCleanupBlocked('), page.indexOf('function s3UrlStyleLabel'))
    expect(summary).not.toContain('listStorageRepositoryAssociatedSources')
    expect(summary).toContain('repositoryCleanupBlockerSummary(preflight.blockers, t)')
  })
})

describe('Repository deletion preflight warning placement', () => {
  it('places warnings before advanced options and uses Local Disk-specific copy', () => {
    const dialog = page.slice(page.indexOf('v-model="deleteRepositoriesDialogOpen"'))
    expect(dialog.indexOf('v-if="deletePreflightMessages.length"')).toBeLessThan(dialog.indexOf('<DangerAdvancedOptions'))
    expect(page).toContain('repo-delete-preflight-alert')
    expect(page).toContain("pendingDeleteRepositories.value.length === 1) return t('repositoriesPage.deleteSingleTitle')")
    expect(page).toContain("'repositoriesPage.batchDeleteProxyFsConfirm'")
    const i18n = createI18n({ legacy: false, locale: 'zh-hans', messages: { en, 'zh-hans': zh } })
    expect(i18n.global.t('repositoriesPage.batchDeleteProxyFsConfirm', { n: 1 })).not.toContain('NAS')
    const warning = repositoryCleanupMessage({ code: 'legacy_local_disk_preserved', detail: 'English legacy warning' }, i18n.global.t)
    expect(warning).toBe(zh.repositoriesPage.cleanupLegacyLocalDiskPreserved)
    expect(warning).not.toContain('English legacy warning')
    const englishWarning = en.repositoriesPage.cleanupLegacyLocalDiskPreserved
    expect(englishWarning).toContain('does not delete disk data')
    expect(englishWarning).toContain('owning Proxy Host')
    expect(englishWarning).toContain('used by other resources')
    expect(englishWarning).toContain('manually clean up')
    expect(englishWarning).toContain('Force Delete also preserves this directory')
  })
})
