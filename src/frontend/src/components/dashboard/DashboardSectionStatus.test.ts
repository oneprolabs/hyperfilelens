// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import DashboardSectionStatus from './DashboardSectionStatus.vue'
import type { DashboardSectionState } from '../../lib/dashboardApi'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: {
  common: { loading: 'Loading…', retry: 'Retry' },
  errors: { generic: { loadFailed: 'Failed to load' }, codes: { authForbidden: 'Access denied' } },
} } })

const ElButton = defineComponent({
  props: { loading: Boolean, disabled: Boolean },
  setup(props, { slots }) {
    return () => h('button', { disabled: props.disabled, 'aria-busy': props.loading }, slots.default?.())
  },
})

describe('Dashboard section feedback', () => {
  it.each([
    ['chip', 1], ['metric', 3], ['list', 3], ['chart', 7],
  ] as const)('shows a static %s placeholder until the first load settles', async (variant, count) => {
    const section = mount(DashboardSectionStatus, {
      props: { state: 'loading', variant }, global: { plugins: [i18n], components: { ElButton } },
    })
    expect(section.get('.section-skeleton').attributes('aria-hidden')).toBe('true')
    expect(section.findAll(`.section-skeleton--${variant} span`)).toHaveLength(count)
    expect(section.get('.sr-only').text()).toBe('Loading…')
    await section.setProps({ state: 'ready' })
    expect(section.find('.dashboard-section-status').exists()).toBe(false)
  })

  it('keeps retry feedback inline until a delayed retry finishes', async () => {
    const state = ref<DashboardSectionState>('unavailable')
    const retrying = ref(false)
    let finish!: () => void
    const request = new Promise<void>(resolve => { finish = resolve })
    const section = mount(defineComponent({
      setup() {
        return () => h(DashboardSectionStatus, {
          state: state.value,
          retrying: retrying.value,
          async onRetry() {
            retrying.value = true
            state.value = 'loading'
            await request
            state.value = 'forbidden'
            retrying.value = false
          },
        })
      },
    }), { global: { plugins: [i18n], components: { ElButton } } })
    await section.get('button').trigger('click')
    expect(section.get('button').attributes()).toMatchObject({ disabled: '', 'aria-busy': 'true' })
    expect(section.find('.section-skeleton').exists()).toBe(false)
    expect(section.get('span:not(.sr-only)').text()).toBe('Failed to load')
    expect(section.get('.sr-only').text()).toBe('Loading…')
    finish()
    await request
    await section.vm.$nextTick()
    expect(section.text()).toBe('Access denied')
    expect(section.find('button').exists()).toBe(false)
  })
})
