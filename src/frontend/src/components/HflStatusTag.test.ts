// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { ElTag } from 'element-plus'
import { describe, expect, it } from 'vitest'
import HflStatusTag from './HflStatusTag.vue'

const global = { components: { ElTag } }

describe('HflStatusTag', () => {
  it('uses the shared compact status class and semantic tone', () => {
    const wrapper = mount(HflStatusTag, {
      props: { label: 'Operational', tone: 'success' },
      global,
    })

    expect(wrapper.element.classList).toContain('hfl-status-tag')
    expect(wrapper.get('.el-tag').classes()).toContain('el-tag--success')
    expect(wrapper.get('.el-tag').classes()).toContain('el-tag--light')
  })

  it('keeps neutral states neutral instead of using the informational blue tone', () => {
    const wrapper = mount(HflStatusTag, {
      props: { label: 'Not Monitored', tone: 'neutral' },
      global,
    })

    expect(wrapper.find('.hfl-tag--neutral').exists()).toBe(true)
    expect(wrapper.get('.el-tag').classes()).not.toContain('el-tag--info')
  })
})
