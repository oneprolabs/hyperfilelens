// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Wizard from './BackupConfigCreateWizard.vue'

describe('backup wizard initialization', () => {
  it('keeps the destination form mounted while loading and disables interaction', async () => {
    const loading = vi.fn()
    const wrapper = mount(Wizard, {
      props: {
        phase: 'form',
        bootstrapping: true,
        steps: [{ step: 0, label: 'Source' }],
        currentStep: 0,
        isStepDone: () => false,
        isStepLocked: () => false,
        isLastStep: false,
        canGoPrev: false,
        title: 'Create backup configuration',
        description: 'Configure a backup',
        ariaLabel: 'Configuration steps',
        waitingText: 'Loading configuration…',
        cancelLabel: 'Cancel',
        prevLabel: 'Previous',
        nextLabel: 'Next',
        confirmLabel: 'Create',
      },
      slots: { form: '<input value="Retained content" />' },
      global: {
        directives: { loading: { mounted: loading, updated: loading } },
        stubs: {
          Teleport: true,
          WizardSteps: true,
          ElButton: {
            props: ['disabled', 'loading'],
            template: '<button :disabled="disabled"><slot /></button>',
          },
        },
      },
    })
    try {
      const main = wrapper.get('main')
      const inputValue = wrapper.get('input').attributes('value')
      expect(main.attributes('aria-busy')).toBe('true')
      expect(wrapper.get('.create-backup-step-body').attributes()).toHaveProperty('inert')
      expect(wrapper.findAll('button').every((button) => button.element.disabled)).toBe(true)
      expect(wrapper.find('wizard-steps-stub').exists()).toBe(true)
      expect(loading.mock.calls[0]?.[1].value).toBe(true)

      await wrapper.setProps({ bootstrapping: false })
      expect(wrapper.get('input').attributes('value')).toBe(inputValue)
      expect(wrapper.get('main').attributes('aria-busy')).toBe('false')
      expect(wrapper.get('.create-backup-step-body').attributes()).not.toHaveProperty('inert')
      expect(wrapper.findAll('button').every((button) => !button.element.disabled)).toBe(true)
      expect(loading.mock.lastCall?.[1].value).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})
