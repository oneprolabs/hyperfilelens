import type { EnrollmentOs } from './nodeApi'
import type { NodeInstallationMode, NodeRole } from '../types/node'

const LINUX_AGENT_ROOT = '/opt/hyperfilelens-agent'
const LINUX_INSTALL_DIR = `${LINUX_AGENT_ROOT}/bin`
const LINUX_DATA_DIR = LINUX_AGENT_ROOT
const MAC_AGENT_ROOT = '/Library/Application Support/HyperFileLens/Agent'
const MAC_USER_AGENT_ROOT = '$HOME/Library/Application Support/HyperFileLens/Agent'
const MAC_LAUNCHD_LABEL = 'com.hyperfilelens.agent'
const WIN_INSTALL_CMD = '& "$env:ProgramData\\HyperFileLens\\Agent\\bin\\install.cmd"'
const WIN_USER_INSTALL_CMD = '& "$env:LOCALAPPDATA\\HyperFileLens\\Agent\\bin\\install.cmd"'

export type NodeLifecycleTab = 'install' | 'upgrade' | 'uninstall' | 'service'

function curlDownloadOptions(tlsVerify: boolean): string {
  return tlsVerify ? "--proto '=https' --tlsv1.2 -fL" : '-k -fL'
}

/** Proxy and gateway nodes are supported on Linux only (NAS mount deps). */
export const LINUX_ONLY_ROLES: NodeRole[] = ['proxy', 'gateway']

export function isLinuxOnlyRole(role: NodeRole): boolean {
  return LINUX_ONLY_ROLES.includes(role)
}

export function roleSupportedOnOs(role: NodeRole, os: EnrollmentOs): boolean {
  if (isLinuxOnlyRole(role)) return os === 'linux'
  return true
}

export function linuxInstallScriptPath() {
  return `${LINUX_INSTALL_DIR}/install.sh`
}

function installScriptPath(os: EnrollmentOs, installationMode: NodeInstallationMode): string {
  if (installationMode === 'user') {
    return os === 'macos'
      ? `"${MAC_USER_AGENT_ROOT}/bin/install.sh"`
      : '"${XDG_DATA_HOME:-$HOME/.local/share}/hyperfilelens-agent/bin/install.sh"'
  }
  return os === 'macos'
    ? `"${MAC_AGENT_ROOT}/bin/install.sh"`
    : linuxInstallScriptPath()
}

export function enrollmentHelperDownloadUrl(
  apiBase: string,
  arch: 'amd64' | 'arm64' = 'amd64',
): string {
  return `${apiBase.replace(/\/$/, '')}/media/enroll-bootstrap/hfl-enroll-linux-${arch}`
}

function inferredEnrollmentHelperUrl(
  downloadUrl: string,
  arch: 'amd64' | 'arm64',
): string {
  try {
    return enrollmentHelperDownloadUrl(new URL(downloadUrl).origin, arch)
  } catch {
    return ''
  }
}

export function buildLocalUpgradeCommand(
  os: EnrollmentOs,
  packagePath: string,
  withDownload = false,
  downloadUrl = '',
  role?: NodeRole,
  tlsVerify = true,
  helperDownloadUrl = '',
  arch: 'amd64' | 'arm64' = 'amd64',
  installationMode: NodeInstallationMode = 'system',
) {
  const pkg = packagePath.trim() || '/path/to/hfl-agent-*.tar.gz'
  if (role === 'gateway' && os === 'linux') {
    const archive = pkg.endsWith('.tar.gz') ? pkg : '/tmp/hfl-agent.tar.gz'
    if (withDownload && downloadUrl) {
      const helperUrl = helperDownloadUrl || inferredEnrollmentHelperUrl(downloadUrl, arch)
      if (!helperUrl) return ''
      const curlOptions = curlDownloadOptions(tlsVerify)
      return `(\nset -e\nHFL_ENROLL_HELPER="$(mktemp /tmp/hfl-enroll.XXXXXX)"\ntrap 'rm -f "$HFL_ENROLL_HELPER"' EXIT\ncurl ${curlOptions} -o "$HFL_ENROLL_HELPER" '${helperUrl}'\nchmod +x "$HFL_ENROLL_HELPER"\ncurl ${curlOptions} -o ${archive} '${downloadUrl}'\nsudo "$HFL_ENROLL_HELPER" gateway-upgrade --from ${archive}\n)`
    }
    return ''
  }
  if (os === 'windows') {
    const installCommand = installationMode === 'user' ? WIN_USER_INSTALL_CMD : WIN_INSTALL_CMD
    const zip = pkg.endsWith('.zip') ? pkg : 'C:\\path\\to\\hfl-agent-*.zip'
    if (withDownload && downloadUrl) {
      const insecure = tlsVerify ? '' : ' -k'
      return `curl.exe${insecure} -fL -o "${zip}" "${downloadUrl}"\r\n${installCommand} upgrade -From "${zip}"`
    }
    return `${installCommand} upgrade -From "${zip}"`
  }
  const archive = pkg.endsWith('.tar.gz') ? pkg : '/tmp/hfl-agent.tar.gz'
  const installScript = installScriptPath(os, installationMode)
  const privilegePrefix = installationMode === 'user' ? '' : 'sudo '
  if (withDownload && downloadUrl) {
    return `curl ${curlDownloadOptions(tlsVerify)} -o ${archive} '${downloadUrl}'\n${privilegePrefix}${installScript} upgrade --from ${archive}`
  }
  return `${privilegePrefix}${installScript} upgrade --from ${archive}`
}

export function buildLocalUninstallCommand(
  os: EnrollmentOs,
  purgeAll = true,
  role?: NodeRole,
  installationMode: NodeInstallationMode = 'system',
) {
  if (role === 'gateway' && os === 'linux') {
    return purgeAll
      ? `sudo ${linuxInstallScriptPath()} uninstall --purge-all`
      : `sudo ${linuxInstallScriptPath()} uninstall`
  }
  if (os === 'windows') {
    const installCommand = installationMode === 'user' ? WIN_USER_INSTALL_CMD : WIN_INSTALL_CMD
    return purgeAll
      ? `${installCommand} uninstall -PurgeAll`
      : `${installCommand} uninstall`
  }
  const installScript = installScriptPath(os, installationMode)
  const privilegePrefix = installationMode === 'user' ? '' : 'sudo '
  return purgeAll
    ? `${privilegePrefix}${installScript} uninstall --purge-all`
    : `${privilegePrefix}${installScript} uninstall`
}

export function buildLocalServiceCommand(
  os: EnrollmentOs,
  action: 'status' | 'start' | 'stop' | 'restart',
  role?: NodeRole,
  installationMode: NodeInstallationMode = 'system',
) {
  if (role === 'gateway' && os === 'linux') {
    const agent = `sudo ${linuxInstallScriptPath()} ${action}`
    const sidecar = 'sudo docker compose -p hyperfilelens-gateway -f /etc/hyperfilelens/lensnode/docker-compose.yml'
    if (action === 'status') return `${agent}\n${sidecar} ps`
    if (action === 'start') return `${agent}\n${sidecar} up -d`
    if (action === 'stop') return `${sidecar} stop\n${agent}`
    return `${agent}\n${sidecar} up -d`
  }
  if (os === 'windows') {
    if (installationMode === 'user') {
      return `${WIN_USER_INSTALL_CMD} ${action}`
    }
    if (installationMode === 'account') {
      // Specified-user continuous mode uses Task Scheduler, not SCM.
      return `${WIN_INSTALL_CMD} ${action}`
    }
    if (action === 'status') return `${WIN_INSTALL_CMD} status`
    if (action === 'start') return 'Start-Service HyperFileLensAgent'
    if (action === 'stop') return 'Stop-Service HyperFileLensAgent -Force'
    return 'Restart-Service HyperFileLensAgent'
  }
  const installScript = installScriptPath(os, installationMode)
  return `${installationMode === 'user' ? '' : 'sudo '}${installScript} ${action}`
}

export function roleDeployNotes(role: NodeRole): string[] {
  switch (role) {
    case 'gateway':
      return ['noteGateway', 'noteGatewayLinuxOnly']
    case 'proxy':
      return ['noteProxy', 'noteProxyLinuxOnly']
    default:
      return []
  }
}

export function defaultPackagePath(
  os: EnrollmentOs,
  version?: string,
  arch: 'amd64' | 'arm64' = 'amd64',
) {
  const ver = version?.trim() || '1.0.0'
  if (os === 'windows') {
    return `$env:TEMP\\hfl-agent-${ver}-windows-${arch}.zip`
  }
  if (os === 'macos') {
    return `/tmp/hfl-agent-${ver}-darwin-${arch}.tar.gz`
  }
  return `/tmp/hfl-agent-${ver}-linux-${arch}.tar.gz`
}

export function installPathsSummary(
  os: EnrollmentOs,
  role?: NodeRole,
  installationMode: NodeInstallationMode = 'system',
) {
  if (installationMode === 'user') {
    if (os === 'windows') {
      return {
        installDir: '%LOCALAPPDATA%\\HyperFileLens\\Agent\\bin',
        dataDir: '%LOCALAPPDATA%\\HyperFileLens\\Agent',
        service: 'HyperFileLensAgent (current-user task)',
      }
    }
    if (os === 'macos') {
      return {
        installDir: '~/Library/Application Support/HyperFileLens/Agent/bin',
        dataDir: '~/Library/Application Support/HyperFileLens/Agent',
        service: `${MAC_LAUNCHD_LABEL} (LaunchAgent)`,
      }
    }
    return {
      installDir: '${XDG_DATA_HOME:-$HOME/.local/share}/hyperfilelens-agent/bin',
      dataDir: '${XDG_DATA_HOME:-$HOME/.local/share}/hyperfilelens-agent',
      service: 'hyperfilelens-agent.service (systemd user)',
    }
  }
  if (installationMode === 'account') {
    if (os === 'windows') {
      return {
        installDir: 'C:\\ProgramData\\HyperFileLens\\Agent\\bin',
        dataDir: 'C:\\ProgramData\\HyperFileLens\\Agent',
        service: 'HyperFileLensAgent (runs as the selected user)',
      }
    }
    if (os === 'macos') {
      return {
        installDir: `${MAC_AGENT_ROOT}/bin`,
        dataDir: MAC_AGENT_ROOT,
        service: `${MAC_LAUNCHD_LABEL} (runs as the selected user)`,
      }
    }
    return {
      installDir: LINUX_INSTALL_DIR,
      dataDir: LINUX_DATA_DIR,
      service: 'hyperfilelens-agent.service (runs as the selected user)',
    }
  }
  if (os === 'windows') {
    return {
      installDir: 'C:\\ProgramData\\HyperFileLens\\Agent\\bin',
      dataDir: 'C:\\ProgramData\\HyperFileLens\\Agent',
      service: 'HyperFileLensAgent',
    }
  }
  if (os === 'macos') {
    return {
      installDir: `${MAC_AGENT_ROOT}/bin`,
      dataDir: MAC_AGENT_ROOT,
      service: `${MAC_LAUNCHD_LABEL} (LaunchDaemon)`,
    }
  }
  if (role === 'gateway') {
    return {
      installDir: `${LINUX_INSTALL_DIR} · /etc/hyperfilelens/lensnode`,
      dataDir: `${LINUX_DATA_DIR} · Data Gateway workspace`,
      service: 'hyperfilelens-agent.service · LensNode container',
    }
  }
  return {
    installDir: LINUX_INSTALL_DIR,
    dataDir: LINUX_DATA_DIR,
    service: 'hyperfilelens-agent.service',
  }
}
