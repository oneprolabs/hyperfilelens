import { describe, expect, it } from 'vitest'
import {
  nodeHasStorageInventorySnapshot,
  nodeHasNetworkStorageInventorySnapshot,
  nodeDiskCount,
  nodeDiskUsageParts,
  nodeStoragePoolRows,
  nodeSupportsStorageInventory,
  proxyNodeStackIpLine,
} from './nodeInventoryDisplay'
import type { ApiNode } from '../types/node'

function nodeWithInventory(inventory: Record<string, unknown>): ApiNode {
  return {
    id: 1,
    organization: 1,
    name: 'proxy-1',
    role: 'proxy',
    status: 'active',
    metadata: { inventory },
  }
}

describe('node host IP display', () => {
  it('prefers the bound Node host IP over a stale cached fallback', () => {
    expect(
      proxyNodeStackIpLine(
        { ip_address: '10.20.1.15' },
        '203.0.113.20',
      ),
    ).toBe('10.20.1.15')
  })

  it('uses the cached fallback only when no Node host IP is available', () => {
    expect(proxyNodeStackIpLine({ ip_address: null }, '10.20.1.15')).toBe('10.20.1.15')
  })
})

describe('node storage pool inventory', () => {
  it('reads structured local storage without falling back to legacy aggregate capacity', () => {
    const node = nodeWithInventory({
      capabilities: ['storage_inventory_v1'],
      storage_inventory_status: 'ready',
      network_storage_inventory_status: 'ready',
      disk_total_bytes: 999,
      local_storage_pools: [{
        key: 'local:device:/dev/sda1',
        device: '/dev/sda1',
        fs_type: 'ext4',
        mount_points: ['/'],
        total_bytes: 40,
        used_bytes: 8,
        free_bytes: 32,
      }],
    })

    expect(nodeStoragePoolRows(node, 'local_storage_pools')).toEqual([{
      key: 'local:device:/dev/sda1',
      device: '/dev/sda1',
      fsType: 'ext4',
      mountPoints: ['/'],
      totalBytes: 40,
      usedBytes: 8,
      availableBytes: 32,
    }])
  })

  it('keeps one network storage row with all unique mount points reported by the Agent', () => {
    const node = nodeWithInventory({
      capabilities: ['storage_inventory_v1'],
      storage_inventory_status: 'ready',
      network_storage_inventory_status: 'ready',
      network_storage_pools: [{
        key: 'network:smb:192.168.7.148/c',
        device: '//192.168.7.148/C',
        fs_type: 'cifs',
        mount_points: ['/mnt/c', '/mnt/desktop', '/mnt/c'],
        total_bytes: 100,
        used_bytes: 20,
        free_bytes: 80,
      }],
    })

    const rows = nodeStoragePoolRows(node, 'network_storage_pools')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.mountPoints).toEqual(['/mnt/c', '/mnt/desktop'])
  })

  it('does not present legacy aggregate capacity as structured local storage', () => {
    const node = nodeWithInventory({
      disk_total_bytes: 338.7,
      disk_used_bytes: 119.6,
      disk_count: 5,
    })

    expect(nodeStoragePoolRows(node, 'local_storage_pools')).toEqual([])
    expect(nodeStoragePoolRows(node, 'network_storage_pools')).toEqual([])
    expect(nodeSupportsStorageInventory(node)).toBe(false)
  })

  it('ignores stale structured fields after an Agent without the capability reports', () => {
    const node = nodeWithInventory({
      capabilities: ['repository_operation_v1'],
      local_storage_pools: [{
        key: 'stale-local-pool',
        total_bytes: 40,
      }],
      network_storage_pools: [{
        key: 'stale-network-pool',
        total_bytes: 100,
      }],
    })

    expect(nodeStoragePoolRows(node, 'local_storage_pools')).toEqual([])
    expect(nodeStoragePoolRows(node, 'network_storage_pools')).toEqual([])
    expect(nodeSupportsStorageInventory(node)).toBe(false)
  })

  it('recognizes an updated Agent even when its latest structured inventory is empty', () => {
    const node = nodeWithInventory({
      capabilities: ['storage_inventory_v1'],
      storage_inventory_status: 'ready',
      network_storage_inventory_status: 'ready',
      local_storage_pools: [],
      network_storage_pools: [],
    })

    expect(nodeSupportsStorageInventory(node)).toBe(true)
    expect(nodeHasStorageInventorySnapshot(node)).toBe(true)
    expect(nodeHasNetworkStorageInventorySnapshot(node)).toBe(true)
    expect(nodeStoragePoolRows(node, 'local_storage_pools')).toEqual([])
  })

  it('distinguishes an updated Agent awaiting its first storage snapshot', () => {
    const node = nodeWithInventory({
      capabilities: ['storage_inventory_v1'],
      storage_inventory_status: 'pending',
      network_storage_inventory_status: 'pending',
      disk_total_bytes: 338.7,
      disk_used_bytes: 119.6,
      disk_count: 5,
      local_storage_pools: [{
        key: 'stale-local-pool',
        total_bytes: 338.7,
      }],
      network_storage_pools: [{
        key: 'stale-network-pool',
        total_bytes: 500,
      }],
    })

    expect(nodeSupportsStorageInventory(node)).toBe(true)
    expect(nodeHasStorageInventorySnapshot(node)).toBe(false)
    expect(nodeHasNetworkStorageInventorySnapshot(node)).toBe(false)
    expect(nodeStoragePoolRows(node, 'local_storage_pools')).toEqual([])
    expect(nodeStoragePoolRows(node, 'network_storage_pools')).toEqual([])
    expect(nodeDiskUsageParts(node)).toEqual({ used: 0, total: 0 })
    expect(nodeDiskCount(node)).toBeNull()
  })

  it('rejects an incomplete ready snapshot without a local pool array', () => {
    const node = nodeWithInventory({
      capabilities: ['storage_inventory_v1'],
      storage_inventory_status: 'ready',
      disk_total_bytes: 338.7,
      disk_used_bytes: 119.6,
      disk_count: 5,
    })

    expect(nodeHasStorageInventorySnapshot(node)).toBe(false)
    expect(nodeDiskUsageParts(node)).toEqual({ used: 0, total: 0 })
    expect(nodeDiskCount(node)).toBeNull()
  })
})
