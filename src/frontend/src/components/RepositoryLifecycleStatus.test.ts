// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import RepositoryLifecycleStatus from './RepositoryLifecycleStatus.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      repositoriesPage: {
        statusResidualActionRequired: 'Residual action required',
        statusRepositoryRecordRemoved: 'Repository record removed',
        residualAttentionTitle: 'Residual storage location requires attention',
        residualAttentionDescription: 'Review retained data before releasing this location.',
        residualReviewAction: 'Review and resolve',
      },
    },
  },
})

function render(initializationState: string, actionable = false) {
  return mount(RepositoryLifecycleStatus, {
    props: {
      status: 'removed',
      initializationState,
      label: 'Removed',
      tagType: 'info',
      actionable,
    },
    global: {
      plugins: [i18n],
      stubs: {
        HflPopover: {
          template: '<div><slot name="reference" /><slot /></div>',
          methods: {
            hide() {},
          },
        },
      },
    },
  })
}

describe('RepositoryLifecycleStatus', () => {
  it('explains retained storage when a removed repository requires attention', () => {
    const wrapper = render('attention_required')

    expect(wrapper.text()).toContain('Residual action required')
    expect(wrapper.text()).toContain('Repository record removed')
    expect(wrapper.find('.el-tag').classes()).toContain('el-tag--warning')
  })

  it('keeps the ordinary lifecycle label when no residual location exists', () => {
    const wrapper = render('released')

    expect(wrapper.text()).toBe('Removed')
    expect(wrapper.find('.repository-lifecycle-status__context').exists()).toBe(false)
    expect(wrapper.find('.repository-lifecycle-status__action').exists()).toBe(false)
    expect(wrapper.find('.el-tag').classes()).toContain('el-tag--info')
  })

  it('opens recovery details from the persistent action', async () => {
    const wrapper = render('attention_required', true)

    expect(wrapper.find('.repository-lifecycle-status__info').attributes('aria-label'))
      .toBe('Residual storage location requires attention')
    expect(wrapper.find('.repository-lifecycle-status__action').text()).toBe('Review and resolve')
    expect(wrapper.find('.repository-lifecycle-status__popover-action').text()).toBe('Review and resolve')

    await wrapper.find('.repository-lifecycle-status__action').trigger('click')

    expect(wrapper.emitted('open')).toHaveLength(1)
  })
})
