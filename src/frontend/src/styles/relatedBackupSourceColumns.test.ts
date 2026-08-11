import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repositoriesPage = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')
const policiesPage = readFileSync(resolve(process.cwd(), 'src/pages/protection/Policies.vue'), 'utf8')

function occurrences(source: string, value: string) {
  return source.split(value).length - 1
}

describe('related backup source detail columns', () => {
  it('keeps the standard DR Target columns in the requested order', () => {
    const tableStart = repositoriesPage.indexOf('class="hfl-list-table repo-associated-sources__table"')
    const tableEnd = repositoriesPage.indexOf('</ElTable>', tableStart)
    const table = repositoriesPage.slice(tableStart, tableEnd)
    const columns = [
      'associatedSourceColId',
      'associatedSourceColSource',
      'associatedSourceColEndpoint',
      'associatedSourceColAvailability',
      'sourceResources.colRegisteredAt',
    ]

    expect(columns.map((column) => table.indexOf(column))).toEqual(
      [...columns.map((column) => table.indexOf(column))].sort((a, b) => a - b),
    )
    expect(table).toContain(':width="isDirectNasAssociatedSources() ? 80 : 90"')
    expect(table).toContain(':min-width="isDirectNasAssociatedSources() ? 160 : 260"')
    expect(table).toMatch(/associatedSourceColNasConnectivity'[\s\S]*?min-width="129"/)
    expect(table).toMatch(/v-if="!isDirectNasAssociatedSources\(\)"[\s\S]*?sourceResources\.colRegisteredAt/)
    expect(table).toContain('associatedSourceStatusTagAttrs(row.availability)')
    expect(table).toContain('associatedSourceStatusLabel(row.availability)')
  })

  it('uses ID, Backup Source, Endpoint, Connectivity, and Registered At for policies and filters', () => {
    expect(occurrences(policiesPage, "t('repositoriesPage.associatedSourceColId')")).toBe(2)
    expect(occurrences(policiesPage, "t('protection.sourceResources.colRegisteredAt')")).toBe(2)

    const tableKeys = [
      'associatedSourceColId',
      'colBackupSource',
      'associatedSourceColEndpoint',
      'associatedSourceColAvailability',
      'colRegisteredAt',
    ]
    for (const resizeKey of ['protection.policies.backup.related', 'protection.policies.filters.related']) {
      const tableStart = policiesPage.indexOf(resizeKey)
      const tableEnd = policiesPage.indexOf('</el-table>', tableStart)
      const table = policiesPage.slice(tableStart, tableEnd)
      const positions = tableKeys.map((key) => table.indexOf(key))
      expect(positions.every((position) => position >= 0)).toBe(true)
      expect(positions).toEqual([...positions].sort((a, b) => a - b))
      expect(table).toContain('{{ row.source_ref_id }}')
      expect(table).toContain("{{ relatedSourceRegisteredAt(row) || '—' }}")
      expect(table).toContain("'hfl-empty-mark': !relatedSourceRegisteredAt(row)")
      expect(table).toContain('relatedSourceAvailability(row)')
      expect(table).toContain('relatedSourceAvailabilityLabel(row)')
    }
  })
})
