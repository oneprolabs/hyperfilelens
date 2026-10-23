/** Admin Data Gateway inventory classification (not provenance / origin). */

export type GatewayInventoryType = 'bundled' | 'installed' | 'private'

export type GatewayInventoryTypeSource = {
  scope?: string | null
  metadata?: Record<string, unknown> | null
}

export function gatewayInventoryType(row: GatewayInventoryTypeSource): GatewayInventoryType {
  const scope = String(row.scope || '').trim().toLowerCase()
  if (scope === 'organization' || scope === 'user') return 'private'
  const metadata = row.metadata
  if (
    metadata
    && metadata.managed_by === 'hfl-installer'
  ) {
    return 'bundled'
  }
  return 'installed'
}
