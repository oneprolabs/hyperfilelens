import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), 'src', relativePath), 'utf8')
}

function ordered(text: string, markers: string[]): boolean {
  let cursor = -1
  for (const marker of markers) {
    cursor = text.indexOf(marker, cursor + 1)
    if (cursor < 0) return false
  }
  return true
}

function declaredTableWidth(text: string): number {
  return [...text.matchAll(/\b(?:min-)?width="(\d+)"/g)]
    .reduce((total, match) => total + Number(match[1]), 0)
}

describe('Production Sites availability presentation', () => {
  const list = source('pages/protection/BackupSources.vue')
  const proxyList = source('pages/node/Nodes.vue')
  const hostStart = list.indexOf(':data="pagedHostAgents"')
  const nasStart = list.indexOf(':data="nasRows"')
  const proxyStart = proxyList.indexOf('v-table-column-resize="isProxyNodesPage')
  const hostTable = list.slice(hostStart, list.indexOf('</el-table>', hostStart))
  const nasTable = list.slice(nasStart, list.indexOf('</el-table>', nasStart))
  const proxyTable = proxyList.slice(proxyStart, proxyList.indexOf('<template v-else>', proxyStart))

  it('keeps host lifecycle status separate and places connectivity before version', () => {
    expect(ordered(hostTable, [
      'colName',
      'colLifecycleStatus',
      'colHostIp',
      'colCapacity',
      'colConnectivity',
      'colVersion',
      'colRegisteredAt',
    ])).toBe(true)
  })

  it('preserves selected hosts while lifecycle polling refreshes the table', () => {
    expect(hostTable).toContain('reserve-selection')
    expect(list).toContain('row-key="id"')
  })

  it('places NAS lifecycle status immediately after name and keeps connectivity before registration', () => {
    expect(ordered(nasTable, [
      'colName',
      'colLifecycleStatus',
      'colProtocol',
      'colConnectivity',
      'colRegisteredAt',
    ])).toBe(true)
  })

  it('does not repeat proxy availability in the NAS status column', () => {
    expect(nasTable).not.toContain('proxyStatus')
  })

  it('places Proxy Hosts status after name and availability before version', () => {
    expect(ordered(proxyTable, [
      'colName',
      'colLifecycleStatus',
      'colHostIp',
      'colCapacity',
      'colConnectivity',
      'colVersion',
      'colRegisteredAt',
    ])).toBe(true)
  })

  it('keeps both desktop table width budgets below 1400 pixels', () => {
    expect(declaredTableWidth(hostTable)).toBeLessThanOrEqual(1400)
    expect(declaredTableWidth(nasTable)).toBeLessThanOrEqual(1400)
  })

  it.each([
    'components/NodeBasicInfoPanel.vue',
    'pages/protection/components/NasSourceDetailDrawer.vue',
  ])('shows connectivity and its observation time in %s', (relativePath) => {
    const detail = source(relativePath)
    expect(detail).toContain('colConnectivity')
    expect(detail).toContain('fieldConnectivityUpdatedAt')
    expect(detail).toContain('availability_updated_at')
  })

  it('uses bound node availability for NAS proxy connectivity', () => {
    for (const relativePath of [
      'composables/useNasSourceListDisplay.ts',
      'pages/protection/BackupSources.vue',
      'pages/protection/components/NasSourceDetailDrawer.vue',
    ]) {
      expect(source(relativePath)).toContain('bound_node_availability')
    }
  })

  it('keeps node lifecycle status and connectivity polling separate', () => {
    const basicPanel = source('components/NodeBasicInfoPanel.vue')
    expect(basicPanel).toContain('`nodeLifecycle.state.${visibleStatus}`')
    expect(basicPanel).toContain("status === 'verification_pending' ? 'upgrading' : status")
    expect(basicPanel).toContain('props.node.availability')

    for (const relativePath of [
      'components/HostSourceDetailDrawer.vue',
      'components/ProxyNodeDetailDrawer.vue',
      'pages/insight/InsightGatewayDetailDrawer.vue',
    ]) {
      const detail = source(relativePath)
      expect(detail).toContain('node.value?.availability')
      expect(detail).toContain("availability === 'online'")
    }
  })
})
