import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/styles/list-page-ui.css'), 'utf8')

describe('list table tag overflow styles', () => {
  it('overrides the table block layout for snapshot status icons and text', () => {
    const tableCell = '.hfl-list-table td.el-table__cell:not(.el-table-column--selection):not(.el-table__expand-column) > .cell'
    const snapshotRule = `${tableCell} .snapshot-status-tag .el-tag__content`
    expect(styles).toContain(snapshotRule)
    expect(styles.slice(styles.indexOf(snapshotRule)).split('}')[0]).toMatch(
      /display:\s*inline-flex;\s*align-items:\s*center;\s*gap:\s*4px;\s*white-space:\s*nowrap;/,
    )
  })

  it('clips tag text horizontally without clipping glyph descenders', () => {
    expect(styles).toMatch(/\.el-tag__content\s*{[^}]*line-height:\s*16px;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s)
    expect(styles).not.toMatch(/\.cell \.el-tag\s*{[^}]*top:\s*-1px;/s)
  })
})
