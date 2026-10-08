import {
  GATEWAY_DIRECTORY_STATUS_FIELDS,
  type LensGatewayDirectoryStatus,
  type LensGatewayInsight,
} from './lensApi'

/** Status responses cannot change directory membership, identity or lifecycle. */
export function applyGatewayDirectoryStatus<T extends LensGatewayInsight>(
  rows: T[],
  patches: LensGatewayDirectoryStatus[],
): T[] {
  const byId = new Map(patches.map((patch) => [patch.id, patch]))
  return rows.map((row) => {
    const patch = byId.get(row.id)
    if (
      !patch
      || patch.gateway_link_id !== row.gateway_link_id
      || (patch.sl_lensnode_uuid || '') !== (row.sl_lensnode_uuid || '')
    ) return row
    const runtime: Partial<LensGatewayInsight> = {}
    for (const key of GATEWAY_DIRECTORY_STATUS_FIELDS) {
      if (Object.hasOwn(patch, key)) {
        Object.assign(runtime, { [key]: patch[key] })
      }
    }
    return { ...row, ...runtime }
  })
}
