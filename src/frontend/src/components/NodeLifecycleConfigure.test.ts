// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import NodeLifecycleWizard from './NodeLifecycleWizard.vue'

vi.mock('../lib/nodeApi', () => ({
  fetchNodeMaintenanceRelease: vi.fn(),
  issueEnrollmentInstall: vi.fn(),
  issueGatewayEnrollmentInstall: vi.fn(),
  issuePlatformGatewayEnrollmentInstall: vi.fn(),
  revokeEnrollmentToken: vi.fn(),
  revokePlatformGatewayEnrollment: vi.fn(),
}))
const Input = defineComponent({
  props: ['modelValue'], emits: ['update:modelValue'],
  template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
})

describe('installed server address maintenance', () => {
  it('generates one Gateway command and disables copying invalid origins', async () => {
    const wrapper = mount(NodeLifecycleWizard, {
      props: { orgKey: 'tenant', nodeId: 3, role: 'gateway', os: 'linux', maintenanceOnly: true, initialTab: 'configure' },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en }, missingWarn: false, fallbackWarn: false })],
        stubs: { ElInput: Input, ElAlert: true, ElCheckbox: true, ElRadio: true, ElRadioGroup: true, ElSelect: true, ElOption: true, I18nT: true },
        directives: { loading: () => undefined },
      },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('New server address')
    const input = wrapper.get('input')
    await input.setValue('https://10.0.100.74:11443')
    expect(wrapper.get('.agent-install-wizard__console-pre').text()).toBe(
      "sudo /opt/hyperfilelens-agent/bin/install.sh configure --server-url 'https://10.0.100.74:11443'",
    )
    await input.setValue('https://10.0.100.74:11444/platform-ops')
    expect(wrapper.get('.agent-install-wizard__console-pre').text()).toBe('')
    wrapper.unmount()
  })
})
