import type { ComposerTranslation } from 'vue-i18n'

import type { ErrorDetailsPayload } from './errors/details'
import type { BackupTargetValidationResult } from './protectionBackupTargetValidationApi'

type ValidationFailureDetailsInput = {
  result: BackupTargetValidationResult
  sourceName: string
  t: ComposerTranslation
}

const proxyRepositoryCodes = new Set([
  'PROXY_REPOSITORY_SERVER_ADDRESS_MISSING',
  'PROXY_REPOSITORY_SERVER_START_FAILED',
  'PROXY_REPOSITORY_SERVER_PORT_EXHAUSTED',
  'PROXY_REPOSITORY_SERVER_UNREACHABLE',
  'PROXY_REPOSITORY_SERVER_CONNECTION_FAILED',
])
const s3ClockSkewCode = 'S3_CLOCK_SKEW'
const mountHelperRemediations = new Set([
  'install_nas_mount_helper',
  'repair_nas_mount_helper',
])

function mountHelperNodeLabel(
  details: NonNullable<BackupTargetValidationResult['details']>,
  t: ComposerTranslation,
): string {
  const name = String(details.execution_node_name || '').trim()
  const address = String(details.execution_node_address || '').trim()
  if (name && address) return `${name} (${address})`
  return name || address || t('protection.backupsPage.targetValidationMountHelperNodeFallback')
}

export function backupTargetValidationFailureSummary({
  result,
  sourceName,
  t,
}: ValidationFailureDetailsInput): string {
  if (result.code === s3ClockSkewCode) {
    return t('protection.backupsPage.targetValidationClockSkewSummary')
  }
  if (proxyRepositoryCodes.has(String(result.code || ''))) {
    return t('protection.backupsPage.targetValidationProxySummary', { source: sourceName })
  }
  return t('protection.backupsPage.targetValidationFailedSummary')
}

export function backupTargetValidationFailureDetails({
  result,
  sourceName,
  t,
}: ValidationFailureDetailsInput): ErrorDetailsPayload {
  const message = String(result.message || '').trim()
    || t('protection.backupsPage.targetValidationFailedFallback')

  if (result.code === s3ClockSkewCode) {
    return {
      title: t('protection.backupsPage.targetValidationClockSkewTitle'),
      summary: backupTargetValidationFailureSummary({ result, sourceName, t }),
      errorCode: result.code,
      issue: t('protection.backupsPage.targetValidationClockSkewIssue', { source: sourceName }),
      reasons: [
        t('protection.backupsPage.targetValidationClockSkewReason'),
      ],
      resolutions: [
        t('protection.backupsPage.targetValidationClockSkewCorrectTime', { source: sourceName }),
        t('protection.backupsPage.targetValidationClockSkewSynchronize', { source: sourceName }),
        t('protection.backupsPage.targetValidationRetryStep'),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...result.details,
      },
    }
  }

  const mountDetails = result.details || {}
  const mountRemediation = String(mountDetails.remediation || '')
  const dependency = String(mountDetails.dependency || '').trim()
  const helper = String(mountDetails.helper || '').trim()
  if (
    result.code === 'NAS_MOUNT_FAILED'
    && mountHelperRemediations.has(mountRemediation)
    && dependency
    && helper
  ) {
    const node = mountHelperNodeLabel(mountDetails, t)
    const repair = mountRemediation === 'repair_nas_mount_helper'
    return {
      title: t('protection.backupsPage.targetValidationFailedTitle'),
      summary: backupTargetValidationFailureSummary({ result, sourceName, t }),
      errorCode: result.code,
      issue: message,
      reasons: [message],
      resolutions: [
        repair
          ? t('protection.backupsPage.targetValidationMountHelperRepair', { node, dependency })
          : t('protection.backupsPage.targetValidationMountHelperInstall', { node, dependency }),
        repair
          ? t('protection.backupsPage.targetValidationMountHelperVerifyUsable', { helper })
          : t('protection.backupsPage.targetValidationMountHelperVerifyAvailable', { helper }),
        t('protection.backupsPage.targetValidationRetryStep'),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...mountDetails,
        agent_message: message,
      },
    }
  }

  if (proxyRepositoryCodes.has(String(result.code || ''))) {
    const details = result.details || {}
    const summary = backupTargetValidationFailureSummary({ result, sourceName, t })
    const proxy = details.proxy_name || t('protection.backupsPage.targetValidationProxyFallback')
    const endpoint = details.endpoint || details.proxy_address || '—'
    const portRange = details.port_range || '51515-52014'
    const addressMissing = result.code === 'PROXY_REPOSITORY_SERVER_ADDRESS_MISSING'
    const portExhausted = result.code === 'PROXY_REPOSITORY_SERVER_PORT_EXHAUSTED'
    const unreachable = result.code === 'PROXY_REPOSITORY_SERVER_UNREACHABLE'
    return {
      title: t('protection.backupsPage.targetValidationProxyTitle'),
      summary,
      errorCode: result.code || undefined,
      issue: addressMissing
        ? t('protection.backupsPage.targetValidationProxyAddressMissingIssue', { proxy })
        : unreachable
          ? t('protection.backupsPage.targetValidationProxyUnreachableIssue', { source: sourceName, endpoint })
          : message,
      reasons: [
        addressMissing
          ? t('protection.backupsPage.targetValidationProxyAddressMissingReason')
          : portExhausted
            ? t('protection.backupsPage.targetValidationProxyPortExhaustedReason', { portRange })
            : unreachable
              ? t('protection.backupsPage.targetValidationProxyNetworkReason', { endpoint })
              : message,
      ],
      resolutions: [
        t('protection.backupsPage.targetValidationProxyCheckAddress', { proxy }),
        t('protection.backupsPage.targetValidationProxyEditAddress', { proxy }),
        t('protection.backupsPage.targetValidationProxyCheckNetwork', { source: sourceName, endpoint }),
        t('protection.backupsPage.targetValidationProxyAllowPorts', { portRange }),
        t('protection.backupsPage.targetValidationRetryStep'),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...details,
        diagnostic: message,
      },
    }
  }

  return {
    title: t('protection.backupsPage.targetValidationFailedTitle'),
    summary: backupTargetValidationFailureSummary({ result, sourceName, t }),
    errorCode: result.code || undefined,
    issue: message,
    reasons: [message],
    resolutions: [
      t('protection.backupsPage.targetValidationGenericResolution', { source: sourceName }),
      t('protection.backupsPage.targetValidationRetryStep'),
    ],
    rawDetail: {
      source: sourceName,
      error_code: result.code,
      agent_message: message,
    },
  }
}
