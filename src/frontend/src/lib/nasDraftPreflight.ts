import { h } from 'vue'
import { ElAlert, ElMessage, ElMessageBox } from 'element-plus'
import { testSourceDraft, type SourceConnectionTestResult } from './sourceApi'
import { usesUtf8Iocharset } from './nasMountOptions'
import type { StorageRepositoryCreatePayload } from './storageRepositoryApi'
import '../components/backupSourceFlowActionDialog.css'

export const SMB_CHARSET_UNAVAILABLE = 'SMB_CHARSET_UNAVAILABLE'
type Translate = (key: string, named?: Record<string, unknown>) => string

export class NasDraftPreflightError extends Error {
  readonly result: SourceConnectionTestResult

  constructor(result: SourceConnectionTestResult) {
    super(result.message || 'NAS connection test failed.')
    this.name = 'NasDraftPreflightError'
    this.result = result
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

async function requireSuccessfulDraftTest(payload: Record<string, unknown>) {
  const result = await testSourceDraft(payload)
  if (!result.success) throw new NasDraftPreflightError(result)
}

export async function preflightSourceNasCreate(payload: Record<string, unknown>) {
  const config = record(payload.config)
  const protocol = String(config.protocol || '').trim().toLowerCase()
  const boundNodeId = Number(payload.bound_node_id || payload.bound_node || 0)
  if (
    String(payload.resource_type || '').trim().toLowerCase() !== 'nas'
    || protocol !== 'smb'
    || boundNodeId <= 0
    || !usesUtf8Iocharset(config.options)
  ) return
  await requireSuccessfulDraftTest(payload)
}

export async function preflightNasRepositoryCreate(payload: StorageRepositoryCreatePayload) {
  const config = record(payload.config)
  const boundNodeId = Number(payload.bind_node_id || 0)
  if (
    payload.repo_type !== 'nas'
    || payload.nas_protocol !== 'smb'
    || payload.bind_node_type !== 'proxy'
    || boundNodeId <= 0
    || !usesUtf8Iocharset(config.mount_options)
  ) return

  await requireSuccessfulDraftTest({
    resource_type: 'nas',
    bound_node_id: boundNodeId,
    config: {
      protocol: 'smb',
      server: String(config.server_address || '').trim(),
      share: String(config.share_path || '').trim(),
      options: String(config.mount_options || '').trim(),
    },
    credentials: {
      username: String(config.smb_username || '').trim(),
      password: String(config.smb_password || ''),
      domain: String(config.smb_domain || '').trim(),
    },
  })
}

export async function showNasDraftPreflightGuidance(
  err: unknown,
  t: Translate,
  proxyName: string,
): Promise<boolean> {
  if (!(err instanceof NasDraftPreflightError)) return false
  if (err.result.error_code !== SMB_CHARSET_UNAVAILABLE) {
    ElMessage.error({
      message: err.result.message || t('protection.sourceResources.nasCreateFailed'),
      grouping: true,
    })
    return true
  }

  const kernel = String(err.result.details?.kernel || '').trim()
  const host = proxyName || 'the host'
  const osName = String(err.result.details?.os_name || '').toLowerCase()
  const osFamily = String(err.result.details?.os_family || '').toLowerCase()
  const rhel = osFamily === 'linux' && /(rhel|red hat|centos|rocky|alma|fedora)/.test(osName)
  const installCommands = rhel
    ? ['sudo dnf install kernel-modules-extra-$(uname -r)', 'sudo modprobe nls_utf8']
    : ['sudo apt-get update', 'sudo apt-get install linux-modules-extra-$(uname -r)', 'sudo modprobe nls_utf8']
  const verifyCommands = [
    'modinfo nls_utf8',
    "lsmod | grep '^nls_utf8'",
  ]
  const repairCommands = [
    'sudo apt-get --fix-broken install',
  ]
  const commandBlock = (commands: string[]) => h(
    'pre',
    { class: 'smb-utf8-preflight-dialog__commands' },
    commands.join('\n'),
  )

  await ElMessageBox.alert(
    h('div', { class: 'hfl-flow-action-dialog__body' }, [
      h(ElAlert, {
        type: 'warning',
        closable: false,
        class: 'smb-utf8-preflight-dialog__alert',
      }, {
        default: () => h('ol', { class: 'smb-utf8-preflight-dialog__alert-list' }, [
          h('li', { class: 'smb-utf8-preflight-dialog__alert-item' }, [
            h('span', { class: 'smb-utf8-preflight-dialog__alert-index' }, '1'),
            h('span', { class: 'smb-utf8-preflight-dialog__alert-text' }, [
              t('protection.sourceResources.smbUtf8MissingIntro', { proxy: host }),
              kernel
                ? h('span', { class: 'smb-utf8-preflight-dialog__kernel' }, ` Kernel: ${kernel}.`)
                : null,
            ]),
          ]),
          h('li', { class: 'smb-utf8-preflight-dialog__alert-item' }, [
            h('span', { class: 'smb-utf8-preflight-dialog__alert-index' }, '2'),
            h('span', { class: 'smb-utf8-preflight-dialog__alert-text' },
              t('protection.sourceResources.smbUtf8MissingAgentUpgrade')),
          ]),
          h('li', { class: 'smb-utf8-preflight-dialog__alert-item' }, [
            h('span', { class: 'smb-utf8-preflight-dialog__alert-index' }, '3'),
            h('span', { class: 'smb-utf8-preflight-dialog__alert-text' },
              t('protection.sourceResources.smbUtf8MissingRisk')),
          ]),
        ]),
      }),
      h('div', { class: 'hfl-flow-action-dialog__section' }, [
        h('p', { class: 'hfl-flow-action-dialog__section-title' }, t('protection.sourceResources.smbUtf8MissingInstallTitle')),
        commandBlock(installCommands),
      ]),
      h('div', { class: 'hfl-flow-action-dialog__section' }, [
        h('p', { class: 'hfl-flow-action-dialog__section-title' }, t('protection.sourceResources.smbUtf8MissingVerifyTitle')),
        commandBlock(verifyCommands),
      ]),
      h('div', { class: 'hfl-flow-action-dialog__section' }, [
        h('p', { class: 'hfl-flow-action-dialog__section-title' }, t('protection.sourceResources.smbUtf8MissingRepairTitle')),
        commandBlock(repairCommands),
        h('p', { class: 'smb-utf8-preflight-dialog__section-hint' }, t('protection.sourceResources.smbUtf8MissingRepairHint')),
      ]),
    ]),
    t('protection.sourceResources.smbUtf8MissingTitle'),
    {
      confirmButtonText: t('protection.sourceResources.smbUtf8MissingClose'),
      dangerouslyUseHTMLString: false,
      customClass: 'smb-utf8-preflight-dialog',
    },
  )
  return true
}
