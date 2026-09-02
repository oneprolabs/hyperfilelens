import { readdirSync, readFileSync } from 'node:fs'
import { extname, join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const sourceRoot = resolve(process.cwd(), 'src')
const styles = readFileSync(resolve(sourceRoot, 'styles/detail-page-ui.css'), 'utf8')

function vueFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) return vueFiles(path)
    return extname(entry.name) === '.vue' ? [path] : []
  })
}

describe('global empty value presentation', () => {
  it('uses one muted, left-aligned presentation for detail and table values', () => {
    const detailRule = styles.match(/\.hfl-detail-row__empty,[\s\S]*?\{([\s\S]*?)\}/)?.[1] ?? ''
    const emptyMarkRule = styles.match(/\.hfl-empty-mark\s*\{([\s\S]*?)\}/)?.[1] ?? ''
    const tableRule = styles.match(/\.el-table \.hfl-empty-mark\s*\{([\s\S]*?)\}/)?.[1] ?? ''

    for (const rule of [detailRule, emptyMarkRule]) {
      expect(rule).toContain('color: var(--color-text-secondary)')
      expect(rule).toContain('font-family: inherit')
      expect(rule).toContain('font-size: 13px')
      expect(rule).toContain('font-weight: 400')
    }
    expect(emptyMarkRule).toContain('text-align: left')
    expect(tableRule).toContain('width: 100%')
    expect(tableRule).toContain('text-align: left')
  })

  it('does not render generic empty branches with legacy or unstyled markup', () => {
    const failures: string[] = []
    const genericEmptyBranch = /<span\b(?=[^>]*\bv-(?:else|else-if)\b)[^>]*>\s*(?:—|\{\{\s*t\('(?:common\.empty|ops\.(?:task|audit)\.emptyMark|protection\.backupDetail\.durationDash)'\)\s*\}\})\s*<\/span>/g
    const legacyEmptyClass = /class="(?:hfl-table-cell-muted|protection-flow-cell-muted)"[^>]*>\s*\{\{\s*t\('(?:common\.empty|ops\.(?:task|audit)\.emptyMark|protection\.backupDetail\.durationDash)'\)/

    for (const file of vueFiles(sourceRoot)) {
      const source = readFileSync(file, 'utf8')
      for (const match of source.matchAll(genericEmptyBranch)) {
        if (!match[0].includes('hfl-empty-mark')) failures.push(`${file}: ${match[0]}`)
      }
      if (legacyEmptyClass.test(source)) failures.push(`${file}: legacy empty-value class`)
    }

    expect(failures).toEqual([])
  })
})
