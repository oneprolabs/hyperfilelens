import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/styles/detail-page-ui.css'), 'utf8')
const capacityCell = readFileSync(resolve(process.cwd(), 'src/components/HflCapacityCell.vue'), 'utf8')
const repositoryPage = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')

describe('responsive detail rows', () => {
  it('left-aligns detail values and empty markers', () => {
    expect(styles).toContain('justify-content: flex-start')
    expect(styles).toContain('text-align: left')
    expect(styles).toContain('.hfl-empty-mark {')
    expect(styles).toContain('align-self: flex-start')
    expect(capacityCell).toContain('<span v-else class="hfl-empty-mark">{{ emptyLabel }}</span>')
    const stackedRepositoryUsageRows = repositoryPage.match(
      /class="hfl-detail-row__value hfl-detail-row__value--stacked"/g,
    )
    expect(stackedRepositoryUsageRows?.length).toBeGreaterThanOrEqual(3)
    expect(repositoryPage).toContain('<RepositoryUsageCell')
    expect(repositoryPage).toContain('variant="detail"')
  })

  it('uses the same label width for regular and full rows in single-column layouts', () => {
    const mobileStyles = styles.slice(styles.indexOf('@media (max-width: 860px)'))

    expect(mobileStyles).toContain('.hfl-detail-row--full,')
    expect(mobileStyles).toContain('.detail-page-row--full {')
    expect(mobileStyles).toContain('grid-template-columns: minmax(112px, 34%) minmax(0, 1fr)')
  })
})
