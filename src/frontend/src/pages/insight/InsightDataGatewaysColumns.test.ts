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

  it('adds Admin-only Source and Workspace Quota columns', () => {
    expect(ordered(table, [
      'insight.dataGateway.colOrigin',
      'protection.sourceResources.colLifecycleStatus',
      'protection.sourceResources.colCapacity',
      'platformOps.engineGateway.colCapacity',
      'protection.sourceResources.colConnectivity',
      'insight.dataGateway.colAiEngine',
    ])).toBe(true)
    expect(table).toContain('platformOps.engineGateway.columnActions')
  })

  it('drops Knowledge Sources, composite Status, and HFL Readiness from the list', () => {
    expect(table).not.toContain('colKnowledgeSources')
    expect(table).not.toContain('HFL Readiness')
    expect(table).not.toContain("protection.sourceResources.colStatus")
    expect(table).toContain('columnActions')
    expect(table).toContain('mode="ai-engine"')
  })
})
