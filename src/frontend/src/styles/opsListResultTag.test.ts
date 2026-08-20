import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/styles/ops-list-ui.css'), 'utf8')
const auditPage = readFileSync(resolve(process.cwd(), 'src/pages/ops/Audit.vue'), 'utf8')
const overflowTitleDirective = readFileSync(resolve(process.cwd(), 'src/directives/tableOverflowTitle.ts'), 'utf8')

describe('operations result tag styles', () => {
  it('renders each stacked cell line with its own ellipsis', () => {
    expect(styles).toMatch(
      /\.hfl-ops-cell-stack__title,\s*\.hfl-ops-cell-stack__meta\s*{[^}]*min-width:\s*0;[^}]*max-width:\s*100%;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s,
    )
  })

  it('keeps the status icon and text on one line', () => {
    expect(styles).toMatch(/\.hfl-list-table[^,{]*\.hfl-ops-result-tag \.el-tag__content\s*{[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;/s)
    expect(styles).toMatch(/\.hfl-ops-result-tag__icon\s*{[^}]*width:\s*14px;[^}]*flex:\s*0 0 14px;/s)
    expect(styles).toMatch(/\.hfl-ops-result-tag__text\s*{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s)
  })

  it('uses the shared table overflow title behavior', () => {
    expect(auditPage).toMatch(/<el-table\s+v-table-column-resize="'ops\.audit'"\s+v-table-overflow-title/)
    expect(auditPage).not.toContain('hfl-ops-result-tag__content')
    expect(auditPage).toContain('hfl-ops-result-tag__text')
    expect(auditPage).toContain(':data-table-overflow-title="displayValue(row.result)"')
    expect(overflowTitleDirective).toContain("cell.querySelectorAll<HTMLElement>('[data-table-overflow-title]')")
    expect(overflowTitleDirective).toContain(
      'Boolean(target.dataset.tableOverflowTitle?.trim()) && isOverflowing(target)',
    )
  })
})
