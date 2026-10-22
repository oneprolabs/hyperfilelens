import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(): string {
  return readFileSync(resolve(process.cwd(), 'src/pages/insight/InsightDataGateways.vue'), 'utf8')
}

function ordered(text: string, markers: string[]): boolean {
  let cursor = -1
  for (const marker of markers) {
    cursor = text.indexOf(marker, cursor + 1)
    if (cursor < 0) return false
  }
  return true
}

describe('Insight / Admin Data Gateways list columns', () => {
  const page = source()
  const tableStart = page.indexOf(':data="visibleRows"')
  const table = page.slice(tableStart, page.indexOf('</el-table>', tableStart))

  it('aligns Insight columns with Source Hosts plus AI Engine', () => {
    expect(ordered(table, [
      'protection.sourceResources.colName',
      'protection.sourceResources.colLifecycleStatus',
      'protection.sourceResources.colHostIp',
      'protection.sourceResources.colCpu',
      'protection.sourceResources.colMemory',
      'protection.sourceResources.colDiskCount',
      'protection.sourceResources.colCapacity',
      'protection.sourceResources.colConnectivity',
      'insight.dataGateway.colAiEngine',
      'protection.sourceResources.colVersion',
      'protection.sourceResources.colRegisteredAt',
    ])).toBe(true)
  })

  it('adds Admin-only Type, Organization, and Workspace Quota columns', () => {
    expect(ordered(table, [
      'insight.dataGateway.colType',
      'insight.dataGateway.colOrganization',
      'protection.sourceResources.colLifecycleStatus',
      'protection.sourceResources.colCapacity',
      'platformOps.engineGateway.colCapacity',
      'protection.sourceResources.colConnectivity',
      'insight.dataGateway.colAiEngine',
    ])).toBe(true)
    expect(table).not.toContain('insight.dataGateway.colOrigin')
    expect(table).not.toContain('platformOps.engineGateway.columnActions')
  })

  it('keeps metric columns left-aligned and status columns centered', () => {
    const cpu = table.slice(table.indexOf('colCpu'))
    const memory = table.slice(table.indexOf('colMemory'))
    const disks = table.slice(table.indexOf('colDiskCount'))
    expect(cpu.slice(0, 180)).not.toContain('align="right"')
    expect(memory.slice(0, 180)).not.toContain('align="right"')
    expect(disks.slice(0, 180)).not.toContain('align="right"')
    expect(table).toMatch(/colLifecycleStatus[\s\S]*?align="center"/)
    expect(table).toMatch(/colConnectivity[\s\S]*?align="center"/)
    expect(table).toMatch(/colAiEngine[\s\S]*?align="center"/)
    expect(table).toMatch(/protection\.sourceResources\.colCapacity[\s\S]*?min-width="180"/)
    expect(table).toMatch(/platformOps\.engineGateway\.colCapacity[\s\S]*?min-width="180"/)
  })

  it('drops Knowledge Sources, composite Status, HFL Readiness, and Actions from the list', () => {
    expect(table).not.toContain('colKnowledgeSources')
    expect(table).not.toContain('HFL Readiness')
    expect(table).not.toContain("protection.sourceResources.colStatus")
    expect(table).not.toContain('columnActions')
    expect(table).toContain('mode="ai-engine"')
  })

  it('links Organization to org detail and shows name without org key', () => {
    expect(table).toContain('`/platform-ops/orgs/${organizationId(row)}`')
    expect(page).toContain("String(row.organization_name || '').trim()")
    expect(page).not.toContain('return name || key')
  })
})
