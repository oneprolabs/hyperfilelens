import type { ComposerTranslation } from 'vue-i18n'

import type { ErrorDetailsPayload } from './errors/details'
import type { BackupTargetValidationResult } from './protectionBackupTargetValidationApi'

type ValidationFailureDetailsInput = {
  result: BackupTargetValidationResult
  sourceName: string
  t: ComposerTranslation
  title?: string
  validationKind?: 'backup' | 'restore'
}

function validationFailureTitle(input: ValidationFailureDetailsInput): string {
  return input.title || input.t('protection.backupsPage.targetValidationFailedTitle')
}

function validationRetryStep(input: ValidationFailureDetailsInput): string {
  return input.t(
    input.validationKind === 'restore'
      ? 'protection.backupsPage.targetValidationRestoreRetryStep'
      : 'protection.backupsPage.targetValidationRetryStep',
  )
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
const nasWriteFailureCodes = new Set([
  'NAS_MOUNT_READ_ONLY',
  'NAS_REPOSITORY_READ_ONLY',
  'NAS_MOUNT_SOURCE_MISMATCH',
  'NAS_WRITE_PERMISSION_DENIED',
  'NAS_REPOSITORY_WRITE_DENIED',
])
const nasOwnershipFailureCode = 'REPOSITORY_OWNERSHIP_INVALID'

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
  title,
  validationKind,
}: ValidationFailureDetailsInput): ErrorDetailsPayload {
  const input = { result, sourceName, t, title, validationKind }
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
        validationRetryStep(input),
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
  const missingCifsUtils = result.code === 'NAS_MOUNT_FAILED'
    && /cifs-utils.*(not installed|missing)|missing.*mount\.cifs/i.test(message)
  if (missingCifsUtils) {
    const fallbackNode = t('protection.backupsPage.targetValidationMountHelperNodeFallback')
    const node = mountHelperNodeLabel(mountDetails, t) === fallbackNode
      ? sourceName
      : mountHelperNodeLabel(mountDetails, t)
    return {
      title: validationFailureTitle({ result, sourceName, t, title }),
      summary: backupTargetValidationFailureSummary({ result, sourceName, t }),
      errorCode: result.code,
      issue: t('protection.backupsPage.targetValidationCifsMissingIssue', { node }),
      reasons: [t('protection.backupsPage.targetValidationCifsMissingReason', { node })],
      resolutions: [
        t('protection.backupsPage.targetValidationCifsMissingInstall', { node }),
        t('protection.backupsPage.targetValidationCifsMissingVerify', { node }),
        validationRetryStep(input),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...mountDetails,
        execution_node: node,
        dependency: 'cifs-utils',
        helper: 'mount.cifs',
        agent_message: message,
      },
    }
  }
  if (
    result.code === 'NAS_MOUNT_FAILED'
    && mountHelperRemediations.has(mountRemediation)
    && dependency
    && helper
  ) {
    const node = mountHelperNodeLabel(mountDetails, t)
    const repair = mountRemediation === 'repair_nas_mount_helper'
    return {
      title: validationFailureTitle({ result, sourceName, t, title }),
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
        validationRetryStep(input),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...mountDetails,
        agent_message: message,
      },
    }
  }

  if (nasWriteFailureCodes.has(String(result.code || ''))) {
    const node = mountHelperNodeLabel(mountDetails, t)
    const sourceMismatch = result.code === 'NAS_MOUNT_SOURCE_MISMATCH'
    return {
      title: validationFailureTitle({ result, sourceName, t, title }),
      summary: t('protection.backupsPage.targetValidationNasWriteSummary'),
      errorCode: result.code || undefined,
      issue: message,
      reasons: [message],
      resolutions: [
        sourceMismatch
          ? t('protection.backupsPage.targetValidationNasVerifySource', { node })
          : t('protection.backupsPage.targetValidationNasGrantWrite', { node }),
        t('protection.backupsPage.targetValidationNasRetryRemount'),
        validationRetryStep(input),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...mountDetails,
        agent_message: message,
      },
    }
  }

  if (result.code === 'SMB_CHARSET_UNAVAILABLE') {
    const node = mountHelperNodeLabel(mountDetails, t)
    const osName = String(mountDetails.execution_node_os_name || '').trim()
    const osFamily = String(mountDetails.os_family || '').trim().toLowerCase()
    const distro = `${osName} ${String(mountDetails.os_version || '')}`.toLowerCase()
    const debian = osFamily === 'linux' && /(ubuntu|debian)/.test(distro)
    const rhel = osFamily === 'linux' && /(rhel|red hat|centos|rocky|alma|fedora)/.test(distro)
    const install = debian
      ? t('protection.backupsPage.targetValidationSmbCharsetInstallDebian', { node })
      : rhel
        ? t('protection.backupsPage.targetValidationSmbCharsetInstallRhel', { node })
        : t('protection.backupsPage.targetValidationSmbCharsetInstallGeneric', { node })
    return {
      title: validationFailureTitle({ result, sourceName, t, title }),
      summary: t('protection.backupsPage.targetValidationSmbCharsetSummary'),
      errorCode: result.code,
      issue: t('protection.backupsPage.targetValidationSmbCharsetIssue', {
        node,
        charset: String(mountDetails.charset || 'utf8'),
        module: String(mountDetails.module || 'nls_utf8'),
        kernel: String(mountDetails.kernel || t('protection.backupsPage.targetValidationSmbCharsetRunningKernel')),
      }),
      reasons: [t('protection.backupsPage.targetValidationSmbCharsetReason')],
      resolutions: [
        install,
        t('protection.backupsPage.targetValidationSmbCharsetLoad', { node }),
        t('protection.backupsPage.targetValidationSmbCharsetVerify', { node }),
        validationRetryStep(input),
      ],
      rawDetail: { source: sourceName, error_code: result.code, ...mountDetails, agent_message: message },
    }
  }

  if (result.code === nasOwnershipFailureCode) {
    return {
      title: validationFailureTitle({ result, sourceName, t, title }),
      summary: t('protection.backupsPage.targetValidationNasOwnershipSummary'),
      errorCode: result.code,
      issue: message,
      reasons: [message],
      resolutions: [
        t('protection.backupsPage.targetValidationNasOwnershipRepair'),
        validationRetryStep(input),
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
        validationRetryStep(input),
      ],
      rawDetail: {
        source: sourceName,
        error_code: result.code,
        ...details,
        diagnostic: message,
      },
    }
  }

  const node = mountHelperNodeLabel(result.details || {}, t) === t('protection.backupsPage.targetValidationMountHelperNodeFallback')
    ? sourceName
    : mountHelperNodeLabel(result.details || {}, t)
  return {
    title: validationFailureTitle({ result, sourceName, t, title }),
    summary: backupTargetValidationFailureSummary({ result, sourceName, t }),
    errorCode: result.code || undefined,
    issue: t('protection.backupsPage.targetValidationUnknownIssue', { node, message }),
    reasons: [t('protection.backupsPage.targetValidationUnknownReason', { node })],
    resolutions: [
      t('protection.backupsPage.targetValidationUnknownCheckAgent', { node }),
      t('protection.backupsPage.targetValidationUnknownCheckRepository', { node }),
      t('protection.backupsPage.targetValidationUnknownResolve', { node }),
      validationRetryStep(input),
    ],
    rawDetail: {
      source: sourceName,
      error_code: result.code,
      ...result.details,
      execution_node: node,
      agent_message: message,
    },
  }
}
