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
    expect(page).toContain("t('repositoriesPage.connectivityNotApplicable')")
    expect(page).toContain('<RepositoryLifecycleStatus')
    expect(page).toContain(':actionable="isRemovedRepositoryWithResidualLocation(row)"')
    expect(page).toContain('@open="openDetail(row)"')
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
      ['connectivityNotApplicable', 'Not applicable'],
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
