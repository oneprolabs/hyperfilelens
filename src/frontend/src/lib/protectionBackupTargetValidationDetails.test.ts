import { describe, expect, it } from 'vitest'
import type { ComposerTranslation } from 'vue-i18n'

import {
  backupTargetValidationFailureDetails,
  backupTargetValidationFailureSummary,
} from './protectionBackupTargetValidationDetails'
import type { BackupTargetValidationResult } from './protectionBackupTargetValidationApi'

const messages: Record<string, string> = {
  'protection.backupsPage.targetValidationFailedSummary': 'Connection validation failed.',
  'protection.backupsPage.targetValidationFailedFallback': 'No diagnostic was returned.',
  'protection.backupsPage.targetValidationFailedTitle': 'Backup target validation failed',
  'protection.backupsPage.targetValidationGenericResolution': 'Check settings on {source}.',
  'protection.backupsPage.targetValidationUnknownIssue': 'The Agent on {node} could not access the repository: {message}',
  'protection.backupsPage.targetValidationUnknownReason': 'The Agent on {node} returned no specific category.',
  'protection.backupsPage.targetValidationUnknownCheckAgent': 'Inspect Agent logs on {node}.',
  'protection.backupsPage.targetValidationUnknownCheckRepository': 'Verify repository connectivity and credentials from {node}.',
  'protection.backupsPage.targetValidationUnknownResolve': 'Correct the reported problem on {node}.',
  'protection.backupsPage.targetValidationMountHelperNodeFallback': 'the node running the Agent',
  'protection.backupsPage.targetValidationMountHelperInstall': "On {node}, install {dependency} using the operating system's package manager.",
  'protection.backupsPage.targetValidationMountHelperRepair': "On {node}, repair or reinstall {dependency} using the operating system's package manager.",
  'protection.backupsPage.targetValidationMountHelperVerifyAvailable': 'Verify that {helper} is available and executable.',
  'protection.backupsPage.targetValidationMountHelperVerifyUsable': 'Verify that {helper} starts successfully.',
  'protection.backupsPage.targetValidationCifsMissingIssue': '{node} cannot mount the SMB repository because cifs-utils is not installed.',
  'protection.backupsPage.targetValidationCifsMissingReason': 'The cifs-utils package on {node} provides the mount.cifs helper required to access SMB repositories.',
  'protection.backupsPage.targetValidationCifsMissingInstall': 'On {node}, install cifs-utils. Ubuntu/Debian: sudo apt update && sudo apt install -y cifs-utils. RHEL/CentOS/Rocky/AlmaLinux: sudo dnf install -y cifs-utils.',
  'protection.backupsPage.targetValidationCifsMissingVerify': 'On {node}, run command -v mount.cifs and confirm it returns an executable path.',
  'protection.backupsPage.targetValidationRestoreRetryStep': 'After correcting the problem on the indicated execution node, return to Restore Targets and retry validation.',
  'protection.backupsPage.targetValidationNasWriteSummary': 'The NAS target is not writable.',
  'protection.backupsPage.targetValidationNasGrantWrite': 'Grant write access for {node}.',
  'protection.backupsPage.targetValidationNasVerifySource': 'Verify the NAS target on {node}.',
  'protection.backupsPage.targetValidationNasRetryRemount': 'Retry to refresh the mount.',
  'protection.backupsPage.targetValidationNasOwnershipSummary': 'The NAS repository location is not ready.',
  'protection.backupsPage.targetValidationNasOwnershipRepair': 'Complete or repair repository initialization.',
  'protection.backupsPage.targetValidationSmbCharsetSummary': 'SMB UTF-8 support is unavailable on the host.',
  'protection.backupsPage.targetValidationSmbCharsetIssue': '{node} cannot mount this SMB share because {charset} requires the {module} kernel module, which is unavailable for the running kernel {kernel}.',
  'protection.backupsPage.targetValidationSmbCharsetReason': 'The host is missing the operating-system kernel module required to handle UTF-8 SMB filenames.',
  'protection.backupsPage.targetValidationSmbCharsetInstallDebian': 'On {node}, install linux-modules-extra-$(uname -r) using apt-get.',
  'protection.backupsPage.targetValidationSmbCharsetInstallRhel': 'On {node}, install kernel-modules-extra-$(uname -r) using dnf (or yum on older systems).',
  'protection.backupsPage.targetValidationSmbCharsetInstallGeneric': 'On {node}, install the Linux package that provides the nls_utf8 module for the running kernel.',
  'protection.backupsPage.targetValidationSmbCharsetLoad': 'On {node}, run: sudo modprobe nls_utf8.',
  'protection.backupsPage.targetValidationSmbCharsetVerify': 'On {node}, verify the module with modinfo nls_utf8 and lsmod | grep nls_utf8.',
  'protection.backupsPage.targetValidationSmbCharsetRunningKernel': 'the running kernel',
  'protection.backupsPage.targetValidationClockSkewSummary': 'Source host time is out of sync.',
  'protection.backupsPage.targetValidationClockSkewTitle': 'Source host time differs from S3.',
  'protection.backupsPage.targetValidationClockSkewIssue': '{source} differs too much from S3.',
  'protection.backupsPage.targetValidationClockSkewReason': 'S3 rejected the signed request timestamp.',
  'protection.backupsPage.targetValidationClockSkewCorrectTime': 'Correct date, time, and time zone on {source}.',
  'protection.backupsPage.targetValidationClockSkewSynchronize': 'Synchronize {source} with NTP or Windows Time.',
  'protection.backupsPage.targetValidationProxySummary': 'Proxy validation failed for {source}.',
  'protection.backupsPage.targetValidationProxyTitle': 'Proxy Repository Server connection failed',
  'protection.backupsPage.targetValidationProxyFallback': 'the selected Proxy Host',
  'protection.backupsPage.targetValidationProxyAddressMissingIssue': '{proxy} has no reachable address.',
  'protection.backupsPage.targetValidationProxyUnreachableIssue': '{source} could not connect to {endpoint}.',
  'protection.backupsPage.targetValidationProxyAddressMissingReason': 'No automatic or custom address is available.',
  'protection.backupsPage.targetValidationProxyPortExhaustedReason': 'No port is free in {portRange}.',
  'protection.backupsPage.targetValidationProxyNetworkReason': '{endpoint} is unreachable.',
  'protection.backupsPage.targetValidationProxyCheckAddress': 'Check the address for {proxy}.',
  'protection.backupsPage.targetValidationProxyEditAddress': 'Edit the address for {proxy}.',
  'protection.backupsPage.targetValidationProxyCheckNetwork': 'Test {source} to {endpoint}.',
  'protection.backupsPage.targetValidationProxyAllowPorts': 'Allow {portRange}.',
  'protection.backupsPage.targetValidationRetryStep': 'Retry validation.',
}

const t = ((key: string, params?: Record<string, unknown>) => {
  let value = messages[key] || key
  for (const [name, replacement] of Object.entries(params || {})) {
    value = value.replaceAll(`{${name}}`, String(replacement))
  }
  return value
}) as ComposerTranslation

describe('backup target validation failure details', () => {
  it('identifies the execution node when cifs-utils is missing from an unstructured NAS error', () => {
    const details = backupTargetValidationFailureDetails({
      result: {
        key: 'restore:agent:52', status: 'failed', code: 'NAS_MOUNT_FAILED',
        message: 'mount SMB share: cifs-utils is not installed (missing mount.cifs helper)',
      },
      sourceName: 'hfl-agent3 (100.92.164.55)', title: 'Restore target validation failed',
      validationKind: 'restore', t,
    })
    expect(details.issue).toContain('hfl-agent3 (100.92.164.55)')
    expect(details.issue).toContain('cifs-utils is not installed')
    expect(details.resolutions[0]).toContain('install cifs-utils')
    expect(details.resolutions[1]).toContain('mount.cifs')
    expect(details.resolutions[2]).toContain('Restore Targets')
    expect(details.rawDetail).toMatchObject({
      execution_node: 'hfl-agent3 (100.92.164.55)', dependency: 'cifs-utils', helper: 'mount.cifs',
    })
  })
  it('allows restore validation to reuse the structured guidance with its own title', () => {
    const details = backupTargetValidationFailureDetails({
      result: {
        key: 'restore:agent:52',
        status: 'failed',
        code: 'NAS_MOUNT_FAILED',
        message: 'cifs-utils is not installed',
        details: {
          remediation: 'install_nas_mount_helper',
          dependency: 'cifs-utils',
          helper: 'mount.cifs',
          execution_node_name: 'hfl-agent2',
          execution_node_address: '100.95.174.36',
        },
      },
      sourceName: 'hfl-agent2 (100.95.174.36)',
      title: 'Restore target validation failed',
      validationKind: 'restore',
      t,
    })

    expect(details.title).toBe('Restore target validation failed')
    expect(details.issue).toContain('hfl-agent2 (100.95.174.36)')
    expect(details.resolutions[0]).toContain('install cifs-utils')
    expect(details.resolutions[1]).toContain('mount.cifs')
  })

  it('explains S3 clock skew and how to synchronize the source host', () => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:52',
      status: 'failed',
      code: 'S3_CLOCK_SKEW',
      message: 'Safe backend clock-skew guidance.',
      details: {
        stage: 'repository_connect',
        remediation: 'synchronize_source_time',
      },
    }

    expect(backupTargetValidationFailureSummary({
      result,
      sourceName: 'cnw2016stdx64',
      t,
    })).toBe('Source host time is out of sync.')

    const details = backupTargetValidationFailureDetails({
      result,
      sourceName: 'cnw2016stdx64',
      t,
    })
    expect(details.title).toBe('Source host time differs from S3.')
    expect(details.issue).toBe('cnw2016stdx64 differs too much from S3.')
    expect(details.reasons).toEqual(['S3 rejected the signed request timestamp.'])
    expect(details.resolutions).toContain('Correct date, time, and time zone on cnw2016stdx64.')
    expect(details.resolutions).toContain('Synchronize cnw2016stdx64 with NTP or Windows Time.')
    expect(details.rawDetail).toMatchObject({
      source: 'cnw2016stdx64',
      error_code: 'S3_CLOCK_SKEW',
      stage: 'repository_connect',
      remediation: 'synchronize_source_time',
    })
  })

  it('explains a missing Proxy Repository Server address and how to configure it', () => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:52',
      status: 'failed',
      code: 'PROXY_REPOSITORY_SERVER_ADDRESS_MISSING',
      message: 'No address is available.',
      details: {
        proxy_name: 'proxy-ubuntu-33',
        address_source: 'unavailable',
        port_range: '51515-52014',
      },
    }

    expect(backupTargetValidationFailureSummary({ result, sourceName: 'cnw2016stdx64', t }))
      .toBe('Proxy validation failed for cnw2016stdx64.')

    const details = backupTargetValidationFailureDetails({
      result,
      sourceName: 'cnw2016stdx64',
      t,
    })
    expect(details.errorCode).toBe('PROXY_REPOSITORY_SERVER_ADDRESS_MISSING')
    expect(details.issue).toContain('proxy-ubuntu-33')
    expect(details.reasons).toEqual(['No automatic or custom address is available.'])
    expect(details.resolutions).toContain('Edit the address for proxy-ubuntu-33.')
    expect(details.resolutions).toContain('Allow 51515-52014.')
    expect(details.rawDetail).toMatchObject({
      source: 'cnw2016stdx64',
      proxy_name: 'proxy-ubuntu-33',
      address_source: 'unavailable',
    })
  })

  it('distinguishes source-to-Proxy network failures from TLS or authentication failures', () => {
    const unreachable: BackupTargetValidationResult = {
      key: 'host:agent:52',
      status: 'failed',
      code: 'PROXY_REPOSITORY_SERVER_UNREACHABLE',
      message: 'dial tcp: i/o timeout',
      details: {
        proxy_name: 'proxy-ubuntu-33',
        endpoint: 'https://192.168.10.33:51515',
        port_range: '51515-52014',
      },
    }
    const connection = {
      ...unreachable,
      code: 'PROXY_REPOSITORY_SERVER_CONNECTION_FAILED',
      message: 'TLS certificate fingerprint mismatch',
    } satisfies BackupTargetValidationResult

    const networkDetails = backupTargetValidationFailureDetails({
      result: unreachable,
      sourceName: 'cnw2016stdx64',
      t,
    })
    expect(networkDetails.issue).toContain('https://192.168.10.33:51515')
    expect(networkDetails.reasons).toEqual(['https://192.168.10.33:51515 is unreachable.'])

    const connectionDetails = backupTargetValidationFailureDetails({
      result: connection,
      sourceName: 'cnw2016stdx64',
      t,
    })
    expect(connectionDetails.issue).toBe('TLS certificate fingerprint mismatch')
    expect(connectionDetails.reasons).toEqual(['TLS certificate fingerprint mismatch'])
  })

  it('identifies the execution node and actionable checks for an unknown failure', () => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:11',
      status: 'failed',
      code: 'NAS_MOUNT_FAILED',
      message: 'mount share: permission denied',
    }

    const details = backupTargetValidationFailureDetails({ result, sourceName: 'host-a', t })
    expect(details.summary).toBe('Connection validation failed.')
    expect(details.issue).toContain('host-a')
    expect(details.issue).toContain(result.message)
    expect(details.reasons).toEqual(['The Agent on host-a returned no specific category.'])
    expect(details.resolutions).toContain('Inspect Agent logs on host-a.')
    expect(details.resolutions).toContain('Verify repository connectivity and credentials from host-a.')
  })

  it('explains SMB UTF-8 dependency failures for the executing Linux host', () => {
    const details = backupTargetValidationFailureDetails({
      result: {
        key: 'host:agent:11',
        status: 'failed',
        code: 'SMB_CHARSET_UNAVAILABLE',
        message: 'raw mount error(79)',
        details: {
          execution_node_name: 'hfl-agent1',
          execution_node_os_name: 'Ubuntu 24.04',
          os_family: 'linux',
          charset: 'utf8',
          module: 'nls_utf8',
          kernel: '6.8.0-71-generic',
        },
      },
      sourceName: 'source-a', validationKind: 'restore',
      t,
    })

    expect(details.issue).toContain('hfl-agent1')
    expect(details.issue).toContain('6.8.0-71-generic')
    expect(details.resolutions).toContain('On hfl-agent1, install linux-modules-extra-$(uname -r) using apt-get.')
    expect(details.resolutions).toContain('On hfl-agent1, run: sudo modprobe nls_utf8.')
    expect(details.resolutions).toContain('After correcting the problem on the indicated execution node, return to Restore Targets and retry validation.')
    expect(details.resolutions.join(' ')).not.toContain('Proxy')
  })

  it('uses RHEL-family package guidance without guessing for unknown Linux', () => {
    const base = {
      key: 'host:agent:11', status: 'failed' as const, code: 'SMB_CHARSET_UNAVAILABLE',
      message: 'raw mount error(79)', details: {
        execution_node_name: 'agent-a', execution_node_os_name: 'CentOS', os_family: 'linux',
        charset: 'utf8', module: 'nls_utf8', kernel: '5.14.0',
      },
    }
    const rhel = backupTargetValidationFailureDetails({ result: base, sourceName: 'source-a', t })
    expect(rhel.resolutions[0]).toContain('kernel-modules-extra-$(uname -r)')
    const generic = backupTargetValidationFailureDetails({
      result: { ...base, details: { ...base.details, execution_node_os_name: 'Custom Linux' } },
      sourceName: 'source-a', t,
    })
    expect(generic.resolutions[0]).toContain('package that provides the nls_utf8 module')
  })

  it('explains NAS write failures using the execution node and remount flow', () => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:11',
      status: 'failed',
      code: 'NAS_REPOSITORY_WRITE_DENIED',
      message: 'The NAS share is mounted, but the repository directory is not writable.',
      details: {
        stage: 'write_precheck',
        remediation: 'grant_write_access',
        execution_node_name: 'agent-a',
        execution_node_address: '10.0.0.20',
      },
    }

    const details = backupTargetValidationFailureDetails({ result, sourceName: 'source-a', t })

    expect(details.summary).toBe('The NAS target is not writable.')
    expect(details.issue).toBe(result.message)
    expect(details.resolutions).toEqual([
      'Grant write access for agent-a (10.0.0.20).',
      'Retry to refresh the mount.',
      'Retry validation.',
    ])
    expect(details.rawDetail).toMatchObject({
      remediation: 'grant_write_access',
      execution_node_name: 'agent-a',
      execution_node_address: '10.0.0.20',
    })
  })

  it('explains an unverified existing NAS repository location', () => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:11',
      status: 'failed',
      code: 'REPOSITORY_OWNERSHIP_INVALID',
      message: 'The existing NAS repository location is not ready for use.',
      details: { stage: 'repository_ownership', remediation: 'repair_repository_ownership' },
    }

    const details = backupTargetValidationFailureDetails({ result, sourceName: 'source-a', t })

    expect(details.summary).toBe('The NAS repository location is not ready.')
    expect(details.resolutions).toEqual([
      'Complete or repair repository initialization.',
      'Retry validation.',
    ])
  })

  it.each([
    ['name and address', 'agent-a', '10.0.0.20', 'agent-a (10.0.0.20)'],
    ['name only', 'agent-a', '', 'agent-a'],
    ['address only', '', '10.0.0.20', '10.0.0.20'],
    ['fallback', '', '', 'the node running the Agent'],
  ])('formats the mount-helper execution node with %s', (_, name, address, nodeLabel) => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:11',
      status: 'failed',
      code: 'NAS_MOUNT_FAILED',
      message: 'mount NFS export: nfs-common is not installed (missing mount.nfs helper)',
      details: {
        stage: 'mount_helper',
        remediation: 'install_nas_mount_helper',
        dependency: 'nfs-common',
        helper: 'mount.nfs',
        execution_node_name: name,
        execution_node_address: address,
      },
    }

    const details = backupTargetValidationFailureDetails({ result, sourceName: 'source-a', t })
    expect(details.resolutions).toEqual([
      `On ${nodeLabel}, install nfs-common using the operating system's package manager.`,
      'Verify that mount.nfs is available and executable.',
      'Retry validation.',
    ])
    expect(details.rawDetail).toMatchObject({
      execution_node_name: name,
      execution_node_address: address,
      dependency: 'nfs-common',
      helper: 'mount.nfs',
    })
  })

  it('explains how to repair an unusable SMB mount helper', () => {
    const result: BackupTargetValidationResult = {
      key: 'host:agent:11',
      status: 'failed',
      code: 'NAS_MOUNT_FAILED',
      message: 'mount SMB share: cifs-utils is installed but not usable',
      details: {
        stage: 'mount_helper',
        remediation: 'repair_nas_mount_helper',
        dependency: 'cifs-utils',
        helper: 'mount.cifs',
        execution_node_name: 'agent-a',
        execution_node_address: '10.0.0.20',
      },
    }

    const details = backupTargetValidationFailureDetails({ result, sourceName: 'source-a', t })
    expect(details.resolutions).toContain(
      "On agent-a (10.0.0.20), repair or reinstall cifs-utils using the operating system's package manager.",
    )
    expect(details.resolutions).toContain('Verify that mount.cifs starts successfully.')
  })
})
