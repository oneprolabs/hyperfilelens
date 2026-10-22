// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import { ALERT_POLICY_TYPES, ALERT_RESOURCE_TYPES, useAlertPolicyLabels } from './useAlertPolicyLabels'

describe('optional alert resource descriptions', () => {
  it.each(['en', 'zh-hans', 'es'])('does not query absent description keys in %s', locale => {
    const messages = locale === 'en' ? en : JSON.parse(readFileSync(resolve(
      process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`,
    ), 'utf8'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    let labels!: ReturnType<typeof useAlertPolicyLabels>
    const wrapper = mount(defineComponent({
      setup() {
        labels = useAlertPolicyLabels()
        return () => null
      },
    }), {
      global: {
        plugins: [createI18n({
          legacy: false, locale, fallbackLocale: 'en', messages: { en, [locale]: messages },
        })],
      },
    })
    try {
      for (const type of ALERT_RESOURCE_TYPES) {
        const description = labels.resourceTypeDescription(type)
        expect(description).toBeTruthy()
        expect(description).toBe(messages.ops.alertsCenter.resourceTypeDescriptions[type])
      }
      for (const type of ALERT_POLICY_TYPES) {
        expect(labels.policyTypeDescription(type)).toBeTruthy()
        expect(labels.policyTypeDescription(type)).toBe(messages.ops.alertsCenter.policyTypeDescriptions[type])
      }
      expect(labels.policyTypeDescription('unknown')).toBe('')
      expect(labels.resourceTypeDescription('unknown')).toBe('')
      expect(labels.resourceTypeDescription(null)).toBe('')
      expect(labels.resourceTypeDescription()).toBe('')
      expect(warn).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
      warn.mockRestore()
    }
  })
})
