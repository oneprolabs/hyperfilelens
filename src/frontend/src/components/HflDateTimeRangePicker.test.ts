// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { defineComponent, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import HflDateTimeRangePicker from './HflDateTimeRangePicker.vue'

const ElDatePickerStub = defineComponent({
  props: {
    shortcuts: {
      type: Array,
      default: () => [],
    },
  },
  template: '<div />',
})

describe('HflDateTimeRangePicker accessibility', () => {
  it('uses the Element Plus value contract for range shortcuts', () => {
    const wrapper = mount(HflDateTimeRangePicker, {
      props: {
        label: 'Monitor time range',
        clearText: 'Clear',
        applyText: 'Apply',
        presets: [{ value: '24h', label: 'Last 24 hours', hours: 24 }],
      },
      global: {
        stubs: { ElDatePicker: ElDatePickerStub },
      },
    })

    const [shortcut] = wrapper.getComponent(ElDatePickerStub).props('shortcuts') as Array<{
      text: string
      value: () => [Date, Date]
      onClick?: unknown
    }>
    const range = shortcut.value()

    expect(shortcut.text).toBe('Last 24 hours')
    expect(shortcut.onClick).toBeUndefined()
    expect(range[0]).toBeInstanceOf(Date)
    expect(range[1]).toBeInstanceOf(Date)
    expect(range[1].getTime() - range[0].getTime()).toBe(24 * 60 * 60 * 1000)
    expect(wrapper.emitted('preset')).toEqual([['24h', 24]])
  })

  it('gives both range inputs unique ids, names, and an accessible label', async () => {
    const wrapper = mount(HflDateTimeRangePicker, {
      props: {
        label: 'Monitor time range',
        clearText: 'Clear',
        applyText: 'Apply',
      },
      global: { plugins: [ElementPlus] },
    })
    await nextTick()

    const inputs = wrapper.findAll('input.el-range-input')
    expect(inputs).toHaveLength(2)

    const ids = inputs.map((input) => input.attributes('id'))
    const names = inputs.map((input) => input.attributes('name'))
    expect(ids.every(Boolean)).toBe(true)
    expect(names.every(Boolean)).toBe(true)
    expect(new Set(ids).size).toBe(2)
    expect(new Set(names).size).toBe(2)
    expect(inputs.map((input) => input.attributes('aria-label'))).toEqual([
      'Monitor time range',
      'Monitor time range',
    ])
  })

  it('handles a preset click through the real Element Plus range panel', async () => {
    const wrapper = mount(HflDateTimeRangePicker, {
      attachTo: document.body,
      props: {
        label: 'Monitor time range',
        clearText: 'Clear',
        applyText: 'Apply',
        presets: [{ value: '24h', label: 'Last 24 hours', hours: 24 }],
      },
      global: { plugins: [ElementPlus] },
    })

    await wrapper.get('input.el-range-input').trigger('click')
    await nextTick()
    await flushPromises()
    const shortcut = document.body.querySelector<HTMLButtonElement>('.el-picker-panel__shortcut')
    expect(shortcut).not.toBeNull()

    shortcut?.click()
    await nextTick()
    await flushPromises()
    expect(wrapper.emitted('preset')).toEqual([['24h', 24]])
    expect(wrapper.emitted('apply')).toBeUndefined()
    wrapper.unmount()
  })
})
