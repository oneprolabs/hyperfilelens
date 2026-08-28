// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import ProtectionStopConfirmDialog from './ProtectionStopConfirmDialog.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en },
})

const ElDialogStub = defineComponent({
  props: {
    modelValue: Boolean,
    zIndex: Number,
    title: String,
  },
  template: '<section data-test="dialog" :data-z-index="zIndex" :data-title="title"><slot /><slot name="footer" /></section>',
})

describe('ProtectionStopConfirmDialog', () => {
  it('renders above nested task detail drawers', () => {
    const wrapper = mount(ProtectionStopConfirmDialog, {
      props: {
        modelValue: true,
        kind: 'backup',
        items: [{ name: 'Backup WIN-2JEH332QPE2' }],
      },
      global: {
        plugins: [i18n],
        directives: {
          tableOverflowTitle: {},
        },
        stubs: {
          ElDialog: ElDialogStub,
        },
      },
    })

    expect(Number(wrapper.get('[data-test="dialog"]').attributes('data-z-index'))).toBeGreaterThan(3200)
    expect(wrapper.get('[data-test="dialog"]').attributes('data-title')).toBe('Stop Backup Task?')
    wrapper.unmount()
  })
})
