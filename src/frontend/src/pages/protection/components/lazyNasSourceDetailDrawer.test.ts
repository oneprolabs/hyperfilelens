// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { i18n } from '../../../i18n'
import { LazyDrawerFrame } from '../../../components/feedback/LazyDrawerFrame'
import { NasSourceDetailDrawer } from './lazyNasSourceDetailDrawer'

const { loadDrawer } = vi.hoisted(() => ({ loadDrawer: vi.fn() }))

vi.mock('./NasSourceDetailDrawer.vue', () => {
  loadDrawer()
  return {
    default: defineComponent({
      emits: ['updated', 'update:modelValue'],
      setup(_, { emit }) {
        return () => h(LazyDrawerFrame, null, () => h('button', { onClick: () => {
          emit('updated')
          emit('update:modelValue', false)
        } }, 'NAS details'))
      },
    }),
  }
})

describe('NAS source detail drawer loading', () => {
  it.each([
    ['pages/protection/BackupSources.vue', 'detailOpen && isNasDetail'],
    ['pages/protection/BackupCreateWizard.vue', 'createNasDetailOpen'],
    ['components/ProxyBoundNasSourcesPanel.vue', 'detailOpen'],
    ['pages/protection/components/BackupSourceDetailLauncher.vue', 'nasOpen'],
  ])('mounts the lazy drawer only when open in %s', (path, condition) => {
    const source = readFileSync(resolve(process.cwd(), 'src', path), 'utf8')
    expect(source).toMatch(/import \{ NasSourceDetailDrawer \} from '[^']*\/lazyNasSourceDetailDrawer'/)
    expect(source).not.toMatch(/from '[^']*\/NasSourceDetailDrawer\.vue'/)
    expect(source).toMatch(new RegExp(`<NasSourceDetailDrawer\\s+v-if="${condition}"`))
  })

  it('loads on first open, forwards events, and reuses the module on reopen', async () => {
    const isOpen = ref(false)
    const updated = vi.fn()
    const wrapper = mount(defineComponent({
      setup: () => () => isOpen.value ? h(NasSourceDetailDrawer, {
        modelValue: isOpen.value,
        'onUpdate:modelValue': (value: boolean) => { isOpen.value = value },
        onUpdated: updated,
      }) : null,
    }), { global: { plugins: [i18n], stubs: { teleport: true } } })

    try {
      await flushPromises()
      expect(loadDrawer).not.toHaveBeenCalled()

      isOpen.value = true
      await flushPromises()
      expect(loadDrawer).toHaveBeenCalledTimes(1)
      expect(wrapper.get('.el-drawer__body button').text()).toBe('NAS details')

      await wrapper.get('.el-drawer__body button').trigger('click')
      expect(updated).toHaveBeenCalledTimes(1)
      expect(wrapper.find('button').exists()).toBe(false)

      isOpen.value = true
      await flushPromises()
      expect(wrapper.get('.el-drawer__body button').text()).toBe('NAS details')
      expect(loadDrawer).toHaveBeenCalledTimes(1)
    } finally {
      wrapper.unmount()
    }
  })
})
