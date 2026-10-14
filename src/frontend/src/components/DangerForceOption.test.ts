// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { ElCheckbox } from 'element-plus'
import { describe, expect, it } from 'vitest'
import DangerForceOption from './DangerForceOption.vue'

describe('DangerForceOption', () => {
  it('uses the source deregistration card, label and explanation presentation', async () => {
    const wrapper = mount(DangerForceOption, {
      props: { label: 'Force Cleanup', hint: 'Record retained resources.', disabled: false },
    })
    expect(wrapper.classes()).toContain('hfl-flow-action-dialog__force-panel')
    expect(wrapper.get('.hfl-flow-action-dialog__force-label').text()).toBe('Force Cleanup')
    expect(wrapper.get('.hfl-flow-action-dialog__force-hint').text()).toBe('Record retained resources.')
    await wrapper.get('input').setValue(true)
    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
    await wrapper.setProps({ disabled: true })
    expect(wrapper.findComponent(ElCheckbox).props('disabled')).toBe(true)
    wrapper.unmount()
  })
})
