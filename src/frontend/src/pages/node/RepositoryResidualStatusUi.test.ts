import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')
const english = readFileSync(resolve(process.cwd(), 'src/locales/en.ts'), 'utf8')
const chinese = readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
)

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

  it('provides matching English and Simplified Chinese status copy', () => {
    expect(english).toContain("statusResidualActionRequired: 'Residual action required'")
    expect(english).toContain("statusRepositoryRecordRemoved: 'Repository record removed'")
    expect(english).toContain("connectivityNotApplicable: 'Not applicable'")
    expect(english).toContain("residualReviewAction: 'Review and resolve'")
    expect(chinese).toContain('"statusResidualActionRequired": "残留待处理"')
    expect(chinese).toContain('"statusRepositoryRecordRemoved": "平台记录已移除"')
    expect(chinese).toContain('"connectivityNotApplicable": "不适用"')
    expect(chinese).toContain('"residualReviewAction": "查看并处理"')
  })
})
