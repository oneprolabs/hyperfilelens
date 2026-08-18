import { readdirSync, readFileSync } from 'node:fs'
import { relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function filesBelow(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return filesBelow(path)
    return /\.(?:ts|vue)$/.test(path) ? [path] : []
  })
}

describe('danger confirmation coverage', () => {
  it('keeps ElMessageBox.confirm limited to non-destructive lightweight confirmations', () => {
    const sourceRoot = resolve(process.cwd(), 'src')
    const actual = filesBelow(sourceRoot)
      .filter((file) => !file.endsWith('.test.ts'))
      .filter((file) => readFileSync(file, 'utf8').includes('ElMessageBox.confirm'))
      .map((file) => relative(sourceRoot, file))
      .sort()

    expect(actual).toEqual([
      'lib/logout.ts',
      'pages/node/RepairNasRepository.vue',
      'pages/ops/AlertIncidents.vue',
      'pages/protection/DataProtection.vue',
      'pages/protection/components/FlowBackupSourceDetailDrawer.vue',
    ].sort())
  })

  it('does not expose raw unknown errors from pages, components, or composables', () => {
    const sourceRoot = resolve(process.cwd(), 'src')
    const unsafe: string[] = []

    for (const directory of ['pages', 'components', 'composables']) {
      for (const file of filesBelow(resolve(sourceRoot, directory))) {
        if (file.endsWith('.test.ts')) continue
        const source = readFileSync(file, 'utf8')
        if (/String\((?:err|error)\)|(?:err|error)\s+instanceof\s+Error\s*\?\s*(?:err|error)\.message/.test(source)) {
          unsafe.push(relative(sourceRoot, file))
        }
      }
    }

    expect(unsafe).toEqual([])
  })
})
