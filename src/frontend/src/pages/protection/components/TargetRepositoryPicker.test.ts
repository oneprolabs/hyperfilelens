// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../../../locales/en'
import TargetRepositoryPicker, { type TargetRepositoryItem } from './TargetRepositoryPicker.vue'

const targets: TargetRepositoryItem[] = [
  { id: 's3', name: 'Production archive', repoType: 'S3', location: 'https://storage.example/archive', health: 'online' },
  { id: 'nas', name: 'NAS archive', repoType: 'NAS', location: 'nas.example:/archive', health: 'unverified', nasProtocol: 'nfs' },
  { id: 'proxy', name: 'Bound NAS', repoType: 'NAS', location: '/backup', bindNodeType: 'proxy', bindNodeId: 1 },
  { id: 'disabled', name: 'Incompatible disk', repoType: 'PROXY_FS', location: '/data', disabled: true,
    disabledReason: 'Requires another Proxy', disabledReasonItems: ['Use a repository on the same Proxy.'] },
]
function render(modelValue = '', attachTo?: HTMLElement) {
  return mount(TargetRepositoryPicker, {
    attachTo,
    props: { modelValue, repoType: '', targets, targetPlaceholder: 'Search', repoTypePlaceholder: 'Type', noDataText: 'No repositories' },
    global: { plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    slots: { 'selection-options': '<div class="endpoint-options">Endpoint choices</div>' },
  })
}

describe('TargetRepositoryPicker', () => {
  it('groups type, protocol and complete location into the secondary line', () => {
    const wrapper = render()
    const rows = wrapper.findAll('.target-repository-picker__row')
    const objectStorageMeta = rows[0]!.get('.target-repository-picker__meta')
    const location = objectStorageMeta.get('.target-repository-picker__location')
    expect(objectStorageMeta.get('.target-repository-picker__type').text()).toBe('Object Storage')
    expect(location.text()).toBe(targets[0]!.location)
    expect(location.attributes('title')).toBe(targets[0]!.location)
    expect(rows[1]!.get('.target-repository-picker__type').text()).toBe('NAS · NFS')
    wrapper.unmount()
  })

  it('renders repositories directly without nested selection overlays', () => {
    const wrapper = render()
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(4)
    expect(wrapper.find('.el-select').exists()).toBe(false)
    expect(wrapper.text()).toContain('Select a repository')
    expect(wrapper.find('.endpoint-options').exists()).toBe(false)
    wrapper.unmount()
  })

  it('emits a choice and shows its details and conditional options after parent update', async () => {
    const wrapper = render()
    await wrapper.get('input[value="s3"]').setValue(true)
    expect(wrapper.emitted('update:modelValue')).toEqual([['s3']])
    await wrapper.setProps({ modelValue: 's3' })
    expect(wrapper.get('.target-repository-picker__detail').text()).toContain('Production archive')
    expect(wrapper.find('.endpoint-options').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps selection while filtering by search or category and can locate it', async () => {
    const wrapper = render('s3')
    await wrapper.get('input.el-input__inner').setValue('NAS archive')
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(1)
    expect(wrapper.get('.target-repository-picker__notice').text()).toContain('outside the current filters')
    expect(wrapper.get('.target-repository-picker__detail').text()).toContain('Production archive')
    await wrapper.get('.target-repository-picker__filter:nth-child(3)').trigger('click')
    expect(wrapper.emitted('update:repoType')).toEqual([['NAS']])
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.setProps({ repoType: 'NAS' })
    await wrapper.get('.target-repository-picker__notice button').trigger('click')
    expect(wrapper.emitted('update:repoType')?.at(-1)).toEqual([''])
    expect((wrapper.get('input.el-input__inner').element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it.each([
    { position: 'below', top: 800, height: 56, initialScroll: 0, expectedScroll: 516 },
    { position: 'above', top: 40, height: 56, initialScroll: 600, expectedScroll: 540 },
    { position: 'already visible', top: 150, height: 56, initialScroll: 300, expectedScroll: 300 },
    { position: 'taller than the viewport', top: 150, height: 300, initialScroll: 0, expectedScroll: 50 },
  ])('locates and focuses a $position selection after restoring a long list', async ({ top, height, initialScroll, expectedScroll }) => {
    const host = document.createElement('div')
    document.body.append(host)
    const wrapper = render('s3', host)
    const originalBounds = HTMLElement.prototype.getBoundingClientRect
    const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('target-repository-picker__results')) return new DOMRect(0, 100, 500, 240)
      if (this.classList.contains('target-repository-picker__row') && this.querySelector('input')?.value === 's3') {
        return new DOMRect(0, top, 500, height)
      }
      return originalBounds.call(this)
    })
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    try {
      await wrapper.setProps({
        targets: [
          ...Array.from({ length: 49 }, (_, index) => ({
            ...targets[0]!, id: `other-${index}`, name: `Other repository ${index}`,
          })),
          targets[0]!,
        ],
      })
      await wrapper.get('input.el-input__inner').setValue('Other repository 0')
      expect(wrapper.find('input[value="s3"]').exists()).toBe(false)
      const results = wrapper.get('.target-repository-picker__results').element as HTMLDivElement
      Object.defineProperty(results, 'clientHeight', { configurable: true, value: 240 })
      results.scrollTop = initialScroll
      const documentScroll = document.documentElement.scrollTop

      await wrapper.get('.target-repository-picker__notice button').trigger('click')

      expect(wrapper.findAll('input[type="radio"]')).toHaveLength(50)
      expect(results.scrollTop).toBe(expectedScroll)
      expect(document.documentElement.scrollTop).toBe(documentScroll)
      expect(document.activeElement).toBe(wrapper.get('input[value="s3"]').element)
      expect(focus).toHaveBeenLastCalledWith({ preventScroll: true })
      expect(wrapper.emitted('update:modelValue')).toBeUndefined()
      expect(wrapper.find('.endpoint-options').exists()).toBe(true)
    } finally {
      wrapper.unmount()
      host.remove()
      bounds.mockRestore()
      focus.mockRestore()
    }
  })

  it('allows inspecting disabled reasons without changing the selection or showing its endpoint options', async () => {
    const wrapper = render('s3')
    expect((wrapper.get('input[value="disabled"]').element as HTMLInputElement).disabled).toBe(true)
    await wrapper.get('.target-repository-picker__disabled-reason button').trigger('click')
    expect(wrapper.get('.target-repository-picker__detail').text()).toContain('Use a repository on the same Proxy.')
    expect(wrapper.find('.endpoint-options').exists()).toBe(false)
    expect(wrapper.get('.target-repository-picker__summary').text()).toContain('Production archive')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.get('.target-repository-picker__detail > button').trigger('click')
    expect(wrapper.find('.endpoint-options').exists()).toBe(true)
    wrapper.unmount()
  })

  it('derives direct and Proxy-bound NAS modes and refreshes through the parent', async () => {
    const wrapper = render('nas')
    expect(wrapper.emitted('update:nasMode')?.at(-1)).toEqual(['per_directory_repo'])
    await wrapper.setProps({ modelValue: 'proxy', refreshable: true })
    expect(wrapper.emitted('update:nasMode')?.at(-1)).toEqual(['single_repo'])
    await wrapper.get('.hfl-refresh-button').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each(['input', 'label'])('returns to the selected details by clicking the selected %s without reassigning it', async (control) => {
    const wrapper = render('s3')
    await wrapper.get('.target-repository-picker__disabled-reason button').trigger('click')
    const selectedRow = wrapper.get('.target-repository-picker__row.is-selected')
    await selectedRow.get(control).trigger('click')
    expect(wrapper.get('.target-repository-picker__detail').text()).toContain('Production archive')
    expect(wrapper.find('.endpoint-options').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it.each(['offline', 'removed'])('distinguishes a selected repository that becomes %s from a filtered result', async (reason) => {
    const wrapper = render('s3')
    await wrapper.setProps({
      targets: targets.filter((target) => target.id !== 's3'),
      selectedTarget: reason === 'offline' ? { ...targets[0]!, health: 'offline' } : null,
    })
    const notice = wrapper.get('.target-repository-picker__notice--unavailable')
    expect(notice.text()).toContain('no longer available')
    expect(notice.text()).not.toContain('outside the current filters')
    expect(notice.find('button').exists()).toBe(false)
    expect(wrapper.find('.endpoint-options').exists()).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    await wrapper.setProps({ targets, selectedTarget: null })
    expect(wrapper.find('.target-repository-picker__notice--unavailable').exists()).toBe(false)
    expect(wrapper.find('.endpoint-options').exists()).toBe(true)
    wrapper.unmount()
  })

  it('reports a selection that becomes incompatible and retains the explanation for review', async () => {
    const wrapper = render('s3')
    await wrapper.setProps({
      targets: targets.map((target) => target.id === 's3'
        ? { ...target, disabled: true, disabledReason: 'No longer compatible with the selected sources.' }
        : target),
    })
    expect(wrapper.get('.target-repository-picker__notice--unavailable').text()).toContain('no longer available')
    expect(wrapper.get('.target-repository-picker__detail').text()).toContain('No longer compatible')
    expect(wrapper.find('.endpoint-options').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows an empty state and updates counts when targets refresh', async () => {
    const wrapper = render()
    await wrapper.setProps({ targets: [] })
    expect(wrapper.text()).toContain('No repositories')
    expect(wrapper.get('.target-repository-picker__results-heading').text()).toContain('0 repositories')
    wrapper.unmount()
  })
})
