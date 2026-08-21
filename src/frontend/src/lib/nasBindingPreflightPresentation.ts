import type { StorageRepositoryBindingOwner, StorageRepositoryBindingPreflight } from './storageRepositoryApi'

type Translate = (key: string, params?: Record<string, unknown>) => string

export type NasBindingPreflightPresentation = {
  title: string
  detail: string
  relatedNode: string
}

function dependencyCount(preflight: StorageRepositoryBindingPreflight, code: string): number {
  const blocker = preflight.dependency_blockers?.find((item) => item.code === code)
  return Math.max(0, Number(blocker?.count || 0))
}

function actionableOwner(preflight: StorageRepositoryBindingPreflight): StorageRepositoryBindingOwner | null {
  const missingCapabilityNode = preflight.missing_capability?.[0]
  if (missingCapabilityNode) {
    return {
      node_id: missingCapabilityNode.node_id,
      node_name: missingCapabilityNode.node_name,
      node_role: missingCapabilityNode.node_role,
      node_online: true,
      claim_state: '',
    }
  }
  return preflight.owners.find((owner) => owner.node_name || owner.node_id) || null
}

function relatedNodeText(preflight: StorageRepositoryBindingPreflight, t: Translate): string {
  if (!['DIRECT_NAS_OWNER_UPGRADE_REQUIRED', 'DIRECT_NAS_OWNER_UNAVAILABLE'].includes(preflight.blocker_code)) {
    return ''
  }
  const owner = actionableOwner(preflight)
  if (!owner) return ''
  const role = owner.node_role === 'proxy'
    ? t('repairNasRepo.nodeRoleProxy')
    : owner.node_role === 'agent'
      ? t('repairNasRepo.nodeRoleAgent')
      : t('repairNasRepo.nodeRoleUnknown')
  const name = owner.node_name || `#${owner.node_id || '?'}`
  return t('repairNasRepo.bindingRelevantNode', { node: `${role} ${name}` })
}

export function nasBindingPreflightPresentation(
  preflight: StorageRepositoryBindingPreflight,
  t: Translate,
): NasBindingPreflightPresentation {
  const relatedNode = relatedNodeText(preflight, t)
  switch (preflight.blocker_code) {
    case 'DIRECT_NAS_BIND_DEPENDENCIES': {
      const associatedSourceCount = dependencyCount(preflight, 'associated_sources')
      if (associatedSourceCount > 0) {
        return {
          title: t('repairNasRepo.bindingBlockedAssociatedTitle'),
          detail: t('repairNasRepo.bindingBlockedAssociatedDetail', { n: associatedSourceCount }),
          relatedNode: '',
        }
      }
      return {
        title: t('repairNasRepo.bindingBlockedDependenciesTitle'),
        detail: t('repairNasRepo.bindingBlockedDependenciesDetail'),
        relatedNode: '',
      }
    }
    case 'DIRECT_NAS_OWNER_UPGRADE_REQUIRED':
      return {
        title: t('repairNasRepo.bindingBlockedUpgradeTitle'),
        detail: t('repairNasRepo.bindingBlockedUpgradeDetail'),
        relatedNode,
      }
    case 'DIRECT_NAS_OWNER_UNAVAILABLE':
      return {
        title: t('repairNasRepo.bindingBlockedOfflineTitle'),
        detail: t('repairNasRepo.bindingBlockedOfflineDetail'),
        relatedNode,
      }
    case 'DIRECT_NAS_ACTIVE_TARGETS':
      return {
        title: t('repairNasRepo.bindingBlockedInUseTitle'),
        detail: t('repairNasRepo.bindingBlockedInUseDetail'),
        relatedNode: '',
      }
    case 'DIRECT_NAS_RESIDUAL_UNVERIFIED':
      return {
        title: t('repairNasRepo.bindingBlockedPreviousUseTitle'),
        detail: t('repairNasRepo.bindingBlockedPreviousUseDetail'),
        relatedNode: '',
      }
    case 'DIRECT_NAS_FAILED_PROVISIONING_RESIDUAL':
      return {
        title: t('repairNasRepo.bindingRecoveryAvailableTitle'),
        detail: t('repairNasRepo.bindingRecoveryAvailableDetail', { n: preflight.claim_count }),
        relatedNode: '',
      }
    default:
      return {
        title: t('repairNasRepo.bindingBlockedUnknownTitle'),
        detail: t('repairNasRepo.bindingBlockedUnknownDetail'),
        relatedNode: '',
      }
  }
}

export function bindingPreflightFromApiError(err: unknown): StorageRepositoryBindingPreflight | null {
  if (!err || typeof err !== 'object') return null
  const apiError = err as {
    code?: unknown
    errorCode?: unknown
    meta?: unknown
  }
  const code = String(apiError.errorCode || apiError.code || '')
  if (code !== 'STORAGE.NAS_BIND_BLOCKED') return null
  if (!apiError.meta || typeof apiError.meta !== 'object') return null
  const blocker = (apiError.meta as Record<string, unknown>).binding_blocker
  if (!blocker || typeof blocker !== 'object') return null
  const candidate = blocker as Partial<StorageRepositoryBindingPreflight>
  if (typeof candidate.blocker_code !== 'string' || !Array.isArray(candidate.owners)) return null
  return blocker as StorageRepositoryBindingPreflight
}
