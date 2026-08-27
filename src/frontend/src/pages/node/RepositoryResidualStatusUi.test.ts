import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')
const zhHans = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
)) as {
  repositoriesPage: Record<string, string>
}

describe('repository residual status UI', () => {
  it('separates retained-location recovery from connectivity', () => {
    expect(page).toContain('isRemovedRepositoryWithResidualLocation(row)')
    expect(page).toContain('<RepositoryLifecycleStatus')
    expect(page).toContain(':actionable="isRemovedRepositoryWithResidualLocation(row)"')
    expect(page).toContain('@open="openDetail(row)"')
  })

  it('keeps Connectivity restricted to the health tri-state', () => {
    const helper = page.slice(page.indexOf('function repoHealthLabel'), page.indexOf('const deleteRepositoriesTitle'))
    expect(helper).toContain('normalizeHealth(String(row.health), row.status)')
    expect(helper).toContain("t('repositoriesPage.healthOnline')")
    expect(helper).toContain("t('repositoriesPage.healthOffline')")
    expect(helper).toContain("t(isRepositoryBoundToProxy(row) ? 'repositoriesPage.healthBoundUnverified' : 'repositoriesPage.healthUnverified')")
    expect(helper).not.toContain('healthAttentionRequired')
    expect(helper).not.toContain('connectivityNotApplicable')
    expect(helper).not.toContain('healthNotInitialized')
  })

  it('labels unused connectivity clearly and provides a hover explanation', () => {
    expect(en.repositoriesPage.healthUnverified).toBe('Not Yet Used')
    expect(en.repositoriesPage.healthUnverifiedHelp).toContain('has not been used yet')
    expect(en.repositoriesPage.healthBoundUnverified).toBe('Unverified')
    expect(en.repositoriesPage.healthBoundUnverifiedHelp).toContain('Proxy is online')
    expect(page).toContain('isRepositoryConnectivityUnverified(row)')
    expect(page).toContain('repositoryConnectivityHelpKey(row)')
    expect(page).toContain('repositoryConnectivityHelpKey(detailRow)')
    expect(zhHans.repositoriesPage.healthUnverified).not.toBe(en.repositoriesPage.healthUnverified)
    expect(zhHans.repositoriesPage.healthUnverifiedHelp).not.toBe(en.repositoriesPage.healthUnverifiedHelp)
    expect(zhHans.repositoriesPage.healthBoundUnverified).not.toBe(en.repositoriesPage.healthBoundUnverified)
    expect(zhHans.repositoriesPage.healthBoundUnverifiedHelp).not.toBe(en.repositoriesPage.healthBoundUnverifiedHelp)
  })

  it('keeps recovery guidance and the release action visible in repository details', () => {
    expect(page).toContain('v-if="isRemovedRepositoryWithResidualLocation(detailRow)"')
    expect(page).toContain("t('repositoriesPage.residualAttentionDescription')")
    expect(page).toContain('class="repo-residual-attention__body"')
    expect(page).toContain('class="repo-residual-attention__actions"')
    expect(page).toContain('class="repo-residual-attention__action"')
    expect(page).toContain('type="warning"\n                plain\n                size="small"')
    expect(page).toContain('@click="openDetailReleaseResidualDialog"')
  })

  it('provides English source copy and Simplified Chinese translations', () => {
    const copy = [
      ['statusResidualActionRequired', 'Residual action required'],
      ['statusRepositoryRecordRemoved', 'Repository record removed'],
      ['residualReviewAction', 'Review and resolve'],
    ] as const

    for (const [key, english] of copy) {
      expect(en.repositoriesPage[key]).toBe(english)
      expect(zhHans.repositoriesPage[key]).toEqual(expect.any(String))
      expect(zhHans.repositoriesPage[key].trim()).not.toBe('')
      expect(zhHans.repositoriesPage[key]).not.toBe(english)
    }
  })
})
