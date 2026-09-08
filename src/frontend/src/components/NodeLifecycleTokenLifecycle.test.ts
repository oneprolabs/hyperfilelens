// @vitest-environment jsdom

import { flushPromises, shallowMount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import NodeLifecycleWizard from './NodeLifecycleWizard.vue'

const mocks = vi.hoisted(() => ({
  issueEnrollmentInstall: vi.fn(),
  issueGatewayEnrollmentInstall: vi.fn(),
  issuePlatformGatewayEnrollmentInstall: vi.fn(),
  revokeEnrollmentToken: vi.fn(),
  revokePlatformGatewayEnrollment: vi.fn(),
  fetchNodeMaintenanceRelease: vi.fn(),
}))

vi.mock('../lib/nodeApi', () => mocks)

function issued(tokenId: number, command: string) {
  return {
    token: `token-${tokenId}`,
    tokenId,
    command,
    tlsVerify: true,
    expiresAt: '2099-01-01T00:00:00Z',
  }
}

function mountWizard() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return shallowMount(NodeLifecycleWizard, {
    props: {
      orgKey: 'tenant-a',
      role: 'agent',
      os: 'linux',
      installOnly: true,
    },
    global: {
      plugins: [i18n],
      stubs: {
        ElAlert: true,
        ElCheckbox: true,
        ElRadio: true,
        ElRadioGroup: true,
      },
      directives: {
        loading: () => undefined,
      },
    },
  })
}

describe('Node lifecycle enrollment token ownership', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.revokeEnrollmentToken.mockResolvedValue(undefined)
    mocks.revokePlatformGatewayEnrollment.mockResolvedValue(undefined)
  })

  it('keeps an already displayed install command valid when the selected OS changes', async () => {
    mocks.issueEnrollmentInstall
      .mockResolvedValueOnce(issued(1, 'install-linux'))
      .mockResolvedValueOnce(issued(2, 'install-windows'))

    const wrapper = mountWizard()
    await flushPromises()
    expect(wrapper.get('.agent-install-wizard__console-pre').text()).toBe('install-linux')

    await wrapper.setProps({ os: 'windows' })
    await flushPromises()

    expect(wrapper.get('.agent-install-wizard__console-pre').text()).toBe('install-windows')
    expect(mocks.revokeEnrollmentToken).not.toHaveBeenCalled()
  })

  it('revokes a token returned by an obsolete request before its command is displayed', async () => {
    let resolveFirst: ((value: ReturnType<typeof issued>) => void) | undefined
    const firstRequest = new Promise<ReturnType<typeof issued>>((resolve) => {
      resolveFirst = resolve
    })
    mocks.issueEnrollmentInstall
      .mockReturnValueOnce(firstRequest)
      .mockResolvedValueOnce(issued(2, 'install-windows'))

    const wrapper = mountWizard()
    await wrapper.setProps({ os: 'windows' })
    await flushPromises()
    expect(wrapper.get('.agent-install-wizard__console-pre').text()).toBe('install-windows')

    resolveFirst?.(issued(1, 'install-linux'))
    await flushPromises()

    expect(mocks.revokeEnrollmentToken).toHaveBeenCalledTimes(1)
    expect(mocks.revokeEnrollmentToken).toHaveBeenCalledWith(1)
    expect(wrapper.get('.agent-install-wizard__console-pre').text()).toBe('install-windows')
  })

  it('revokes a pending token that returns after the install page is closed', async () => {
    let resolveRequest: ((value: ReturnType<typeof issued>) => void) | undefined
    const request = new Promise<ReturnType<typeof issued>>((resolve) => {
      resolveRequest = resolve
    })
    mocks.issueEnrollmentInstall.mockReturnValueOnce(request)

    const wrapper = mountWizard()
    wrapper.unmount()
    resolveRequest?.(issued(3, 'install-after-close'))
    await flushPromises()

    expect(mocks.revokeEnrollmentToken).toHaveBeenCalledTimes(1)
    expect(mocks.revokeEnrollmentToken).toHaveBeenCalledWith(3)
  })
})
