import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StorageRepositoryCreatePayload } from './storageRepositoryApi'

const mocks = vi.hoisted(() => ({
  testSourceDraft: vi.fn(),
  alert: vi.fn(),
  messageError: vi.fn(),
}))

vi.mock('./sourceApi', () => ({
  testSourceDraft: mocks.testSourceDraft,
}))

vi.mock('element-plus', () => ({
  ElAlert: { name: 'ElAlert' },
  ElMessageBox: { alert: mocks.alert },
  ElMessage: { error: mocks.messageError },
}))

import {
  NasDraftPreflightError,
  preflightNasRepositoryCreate,
  preflightSourceNasCreate,
  showNasDraftPreflightGuidance,
} from './nasDraftPreflight'

function renderedText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(renderedText).join(' ')
  if (!value || typeof value !== 'object') return ''
  const children = (value as { children?: unknown }).children
  if (children && typeof children === 'object' && 'default' in children) {
    const defaultSlot = (children as { default?: unknown }).default
    if (typeof defaultSlot === 'function') return renderedText(defaultSlot())
  }
  return renderedText(children)
}

describe('NAS draft preflight', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.testSourceDraft.mockResolvedValue({ success: true, message: 'ok' })
    mocks.alert.mockResolvedValue(undefined)
  })

  it('tests a Proxy-bound SMB Source when iocharset=utf8 is configured', async () => {
    const payload = {
      resource_type: 'nas',
      bound_node_id: 17,
      config: { protocol: 'smb', options: 'rw,iocharset=utf8' },
    }

    await preflightSourceNasCreate(payload)

    expect(mocks.testSourceDraft).toHaveBeenCalledWith(payload)
  })

  it.each([
    { resource_type: 'nas', bound_node_id: 17, config: { protocol: 'nfs', options: '' } },
    { resource_type: 'nas', bound_node_id: 17, config: { protocol: 'smb', options: 'rw,vers=3.0' } },
    { resource_type: 'nas', bound_node_id: null, config: { protocol: 'smb', options: 'iocharset=utf8' } },
  ])('skips Source payload %# when preflight does not apply', async (payload) => {
    await preflightSourceNasCreate(payload)
    expect(mocks.testSourceDraft).not.toHaveBeenCalled()
  })

  it('translates a Proxy-bound SMB Repository into a Source draft test', async () => {
    const payload: StorageRepositoryCreatePayload = {
      name: 'SMB repository',
      repo_type: 'nas',
      nas_protocol: 'smb',
      bind_node_type: 'proxy',
      bind_node_id: 23,
      config: {
        server_address: '192.168.8.82',
        share_path: 'smb-share',
        mount_options: 'rw,iocharset=utf8',
        smb_username: 'backup',
        smb_password: 'secret',
        smb_domain: 'LAB',
      },
    }

    await preflightNasRepositoryCreate(payload)

    expect(mocks.testSourceDraft).toHaveBeenCalledWith({
      resource_type: 'nas',
      bound_node_id: 23,
      config: {
        protocol: 'smb',
        server: '192.168.8.82',
        share: 'smb-share',
        options: 'rw,iocharset=utf8',
      },
      credentials: {
        username: 'backup',
        password: 'secret',
        domain: 'LAB',
      },
    })
  })

  it.each([
    { repo_type: 'nas', nas_protocol: 'nfs', bind_node_type: 'proxy', bind_node_id: 1, config: {} },
    { repo_type: 'nas', nas_protocol: 'smb', bind_node_id: undefined, config: { mount_options: 'iocharset=utf8' } },
    { repo_type: 'nas', nas_protocol: 'smb', bind_node_type: 'proxy', bind_node_id: 1, config: { mount_options: 'rw' } },
  ] satisfies StorageRepositoryCreatePayload[])('skips Repository payload %# when preflight does not apply', async (payload) => {
    await preflightNasRepositoryCreate(payload)
    expect(mocks.testSourceDraft).not.toHaveBeenCalled()
  })

  it('throws a typed error when the draft connection test fails', async () => {
    mocks.testSourceDraft.mockResolvedValue({
      success: false,
      message: 'UTF-8 charset is unavailable',
      error_code: 'SMB_CHARSET_UNAVAILABLE',
      details: { kernel: '6.8.0-71-generic' },
    })

    await expect(preflightSourceNasCreate({
      resource_type: 'nas',
      bound_node_id: 17,
      config: { protocol: 'smb', options: 'iocharset=utf8' },
    })).rejects.toBeInstanceOf(NasDraftPreflightError)
  })

  it('shows installation guidance for an SMB charset failure', async () => {
    const handled = await showNasDraftPreflightGuidance(
      new NasDraftPreflightError({
        success: false,
        message: 'mount error(79)',
        error_code: 'SMB_CHARSET_UNAVAILABLE',
        details: { charset: 'utf8' },
      }),
      (key) => key,
      'proxy-alpha',
    )

    expect(handled).toBe(true)
    expect(mocks.alert).toHaveBeenCalledOnce()
    expect(mocks.messageError).not.toHaveBeenCalled()
    expect(String(mocks.alert.mock.calls[0][1]))
      .toBe('protection.sourceResources.smbUtf8MissingTitle')
    expect(mocks.alert.mock.calls[0][2]).toMatchObject({
      customClass: 'smb-utf8-preflight-dialog',
      dangerouslyUseHTMLString: false,
    })
    const content = renderedText(mocks.alert.mock.calls[0][0])
    expect(content).toContain('protection.sourceResources.smbUtf8MissingAgentUpgrade')
    expect(content).toContain('protection.sourceResources.smbUtf8MissingRisk')
    expect(content).toContain('sudo apt-get install linux-modules-extra-$(uname -r)')
    expect(content).toContain('sudo apt-get --fix-broken install')
  })

  it('shows the backend message for any other draft preflight failure', async () => {
    const handled = await showNasDraftPreflightGuidance(
      new NasDraftPreflightError({
        success: false,
        message: 'mount SMB share: permission denied',
      }),
      (key) => key,
      'proxy-alpha',
    )

    expect(handled).toBe(true)
    expect(mocks.messageError).toHaveBeenCalledWith({
      message: 'mount SMB share: permission denied',
      grouping: true,
    })
    expect(mocks.alert).not.toHaveBeenCalled()
  })

  it('does not handle errors outside the draft preflight flow', async () => {
    const handled = await showNasDraftPreflightGuidance(
      new Error('request failed'),
      (key) => key,
      'proxy-alpha',
    )

    expect(handled).toBe(false)
    expect(mocks.alert).not.toHaveBeenCalled()
    expect(mocks.messageError).not.toHaveBeenCalled()
  })
})
