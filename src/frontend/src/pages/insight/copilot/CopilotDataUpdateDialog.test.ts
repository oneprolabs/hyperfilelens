// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ElButton } from 'element-plus'
import { defineComponent } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatLocalDateTime } from '../../../lib/dateTime'
import type { LensSessionLink } from '../../../lib/lensApi'
import { en } from '../../../locales/en'
import CopilotDataUpdateDialog from './CopilotDataUpdateDialog.vue'

const mocks = vi.hoisted(() => ({
  listSnapshots: vi.fn(),
  getSnapshot: vi.fn(),
  update: vi.fn(),
  abandon: vi.fn(),
}))

vi.mock('../../../lib/lensApi', () => ({
  updateCopilotChatData: mocks.update,
  abandonCopilotChatDataUpdate: mocks.abandon,
}))
vi.mock('../../../lib/protectionBackupConfigApi', () => ({
  listBackupSourceSnapshots: mocks.listSnapshots,
  getBackupSourceSnapshot: mocks.getSnapshot,
}))
vi.mock('../../../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const DialogStub = defineComponent({
  props: {
    modelValue: Boolean,
    title: String,
    width: String,
    alignCenter: Boolean,
    appendToBody: Boolean,
  },
  template: '<section><h2>{{ title }}</h2><slot /><footer><slot name="footer" /></footer></section>',
})
const SelectStub = defineComponent({
  props: ['modelValue', 'placeholder', 'disabled', 'loading'],
  emits: ['update:modelValue'],
  template: '<div><slot name="header" /><select :value="modelValue" :disabled="disabled" @change="$emit(\'update:modelValue\', Number($event.target.value))"><option value="">{{ placeholder }}</option><slot /></select><slot name="footer" /></div>',
})
const OptionStub = defineComponent({
  props: ['value', 'label'],
  template: '<option :value="value"><slot>{{ label }}</slot></option>',
})
const session = {
  id: 7, title: 'Backup Chat', backup_config_id: 10,
  backup_source_snapshot_id: 70,
  knowledge_source: 12, lifecycle_status: 'ready', status: 'active',
} as LensSessionLink
const createdAt = '2026-10-08T16:09:20+08:00'
function snapshot(id: number, overrides = {}) {
  return {
    id, snapshot_uid: `bss-${id}`, backup_config_id: 10,
    status: 'available', created_at: createdAt, total_size_bytes: 1024,
    ...overrides,
  }
}
const InputStub = defineComponent({
  props: ['modelValue'],
  emits: ['update:modelValue', 'input'],
  template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value); $emit(\'input\', $event.target.value)">',
})

function mountDialog(row = session) {
  return mount(CopilotDataUpdateDialog, {
    props: { modelValue: false, session: row, sharedCount: 2 },
    global: {
      plugins: [createI18n({
        legacy: false, locale: 'en', messages: { en },
        missingWarn: false, fallbackWarn: false,
      })],
      components: { ElButton },
      stubs: {
        ElDialog: DialogStub,
        ElSelect: SelectStub,
        ElOption: OptionStub,
        ElInput: InputStub,
        DangerConfirmDialog: true,
      },
    },
  })
}

describe('CopilotDataUpdateDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.getSnapshot.mockResolvedValue(snapshot(70, { created_at: '2026-10-07T16:09:20+08:00' }))
    mocks.listSnapshots.mockResolvedValue({
      count: 2,
      results: [
        snapshot(71),
        snapshot(72, { status: 'partial' }),
      ],
    })
  })
  afterEach(() => { vi.useRealTimers() })

  it('uses the standard centered form dialog and places the scope description above the field', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    const dialog = wrapper.findComponent(DialogStub)
    expect(dialog.classes()).toContain('hfl-flow-action-dialog--form')
    expect(dialog.props('width')).toBe('min(680px, calc(100vw - 32px))')
    expect(dialog.props('alignCenter')).toBe(true)
    expect(dialog.props('appendToBody')).toBe(true)
    expect(wrapper.get('h2').text()).toBe('Update Chat Data')
    expect(wrapper.get('.chat-update-description').text()).toContain('2 Chats')
    expect(wrapper.get('.chat-update-description').text()).toContain(en.insight.copilot.dataUpdateConsistency)
    expect(wrapper.get('label').text()).toBe('Target Snapshot')
    expect(wrapper.get('select').text()).toContain('Select a snapshot')
    expect(wrapper.get('select').text()).toContain(`bss-71 · ${formatLocalDateTime(createdAt)} · 1.00 KB`)
    expect(mocks.listSnapshots).toHaveBeenCalledWith(expect.objectContaining({
      ordering: 'picker_latest', status: 'available,partial', page_size: 30,
      exclude_snapshot_id: 70,
    }))
    expect(wrapper.get('select').element.value).toBe('71')
    expect(wrapper.get('.chat-update-body').element.firstElementChild?.className).toBe('chat-update-description')
    wrapper.unmount()
  })

  it('retains explicit snapshot selection and the existing update request', async () => {
    const update = { status: 'pending', target_snapshot_id: 71 }
    mocks.update.mockResolvedValue(update)
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    const button = wrapper.findAll('footer button').find((row) => row.text() === 'Update Chat Data')!
    expect(button.attributes('disabled')).toBeUndefined()
    await wrapper.get('select').setValue('71')
    await button.trigger('click')
    await flushPromises()
    expect(mocks.update).toHaveBeenCalledWith(7, 71)
    expect(wrapper.emitted('saved')).toContainEqual([12, update])
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
    wrapper.unmount()
  })

  it('includes the applied snapshot for comparison with distinct state and role colors', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    const current = wrapper.get('option[value="70"]')
    expect(current.text()).toContain('bss-70')
    expect(current.text()).toContain('Current')
    expect(current.attributes('disabled')).toBeDefined()
    expect(current.get('.hfl-snapshot-choice__available').find('.el-tag--success').exists()).toBe(true)
    expect(current.get('.hfl-snapshot-choice__role').find('.el-tag--primary').exists()).toBe(true)
    expect(wrapper.get('option[value="72"] .hfl-snapshot-choice__partial').find('.el-tag--warning').exists()).toBe(true)
    expect(wrapper.findAll('select option').map((option) => option.attributes('value'))).toEqual(['', '72', '71', '70'])
    expect(wrapper.get('.chat-update-picker-footer').text()).toContain('2 / 2')
    await wrapper.get('select').setValue('70')
    const updateButton = wrapper.findAll('footer button').find((button) => button.text() === 'Update Chat Data')!
    expect(updateButton.attributes('disabled')).toBeDefined()
    await updateButton.trigger('click')
    expect(mocks.update).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('retains the fixed target and recovery actions for a failed update', async () => {
    const row = {
      ...session,
      data_update: { status: 'failed', target_snapshot_id: 999, error: 'Conversion failed' },
    } as LensSessionLink
    const wrapper = mountDialog(row)
    mocks.getSnapshot.mockImplementation((id: number) => Promise.resolve(snapshot(id)))
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('h2').text()).toBe('Retry Data Update')
    expect(wrapper.get('select').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('select').text()).toContain('bss-999')
    expect(mocks.getSnapshot).toHaveBeenCalledWith(999)
    expect(mocks.listSnapshots).not.toHaveBeenCalled()
    const applied = wrapper.get('option[value="70"]')
    expect(applied.attributes('disabled')).toBeDefined()
    expect(applied.get('.hfl-snapshot-choice__role').text()).toBe('Last Applied')
    expect(applied.get('.hfl-snapshot-choice__role').classes()).toContain('hfl-tag--neutral')
    expect(wrapper.get('option[value="999"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('input[aria-label="Search Snapshot ID"]').exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toBe('Conversion failed')
    expect(wrapper.findAll('footer button').map((button) => button.text())).toEqual([
      'Abandon Update', 'Cancel', 'Retry Data Update',
    ])
    await wrapper.get('select').setValue('70')
    const retryButton = wrapper.findAll('footer button').find((button) => button.text() === 'Retry Data Update')!
    expect(retryButton.attributes('disabled')).toBeDefined()
    await retryButton.trigger('click')
    expect(mocks.update).not.toHaveBeenCalled()
    await wrapper.get('select').setValue('999')
    mocks.update.mockResolvedValue({ status: 'pending', target_snapshot_id: 999 })
    await retryButton.trigger('click')
    await flushPromises()
    expect(mocks.update).toHaveBeenCalledWith(7, 999)
    wrapper.unmount()
  })

  it('does not automatically roll back when the applied snapshot is already latest', async () => {
    mocks.getSnapshot.mockResolvedValue(snapshot(70, { created_at: '2026-10-09T16:09:20+08:00' }))
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('')
    expect(wrapper.get('select').text()).toContain('bss-71')
    await wrapper.get('select').setValue('71')
    expect(wrapper.get('select').element.value).toBe('71')
    wrapper.unmount()
  })

  it('marks Partial snapshots but defaults to the latest newer Available', async () => {
    mocks.listSnapshots.mockResolvedValue({
      count: 2, results: [snapshot(72, { status: 'partial' }), snapshot(71)],
    })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('71')
    expect(wrapper.get('.hfl-snapshot-choice__partial').text()).toBe('Partial')
    await wrapper.get('select').setValue('72')
    expect(wrapper.text()).toContain(en.insight.copilot.snapshotPartialHint)
    wrapper.unmount()
  })

  it('does not default to Partial when no complete update snapshot exists', async () => {
    mocks.listSnapshots.mockResolvedValueOnce({
      count: 1, results: [snapshot(72, { status: 'partial' })],
    }).mockResolvedValueOnce({ count: 0, results: [] })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(mocks.listSnapshots).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'available', exclude_snapshot_id: 70, page_size: 1,
    }))
    expect(wrapper.get('select').element.value).toBe('')
    expect(wrapper.text()).toContain(en.insight.copilot.snapshotPartialHint)
    await wrapper.get('select').setValue('72')
    expect(wrapper.get('select').element.value).toBe('72')
    wrapper.unmount()
  })

  it('pins an Available default from a later page without changing the Partial page counts', async () => {
    mocks.listSnapshots.mockResolvedValueOnce({
      count: 31, results: [snapshot(90, { status: 'partial' })],
    }).mockResolvedValueOnce({ count: 1, results: [snapshot(71)] })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('71')
    expect(wrapper.get('select').text()).toContain('bss-90')
    expect(wrapper.get('select').text()).toContain('bss-71')
    expect(wrapper.get('.chat-update-picker-footer').text()).toContain('1 / 31')
    wrapper.unmount()
  })

  it('keeps the applied version excluded when its metadata cannot be loaded and requires an explicit choice', async () => {
    mocks.getSnapshot.mockRejectedValue(new Error('Detail unavailable'))
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('')
    expect(wrapper.get('select').text()).toContain('bss-71')
    expect(wrapper.get('option[value="70"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('option[value="70"]').text()).toContain('#70')
    expect(wrapper.get('option[value="70"]').text()).toContain('Current')
    wrapper.unmount()
  })

  it('refreshes the picker when another shared Chat changes the applied snapshot', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('71')
    mocks.getSnapshot.mockResolvedValue(snapshot(71, { created_at: '2026-10-09T16:09:20+08:00' }))
    mocks.listSnapshots.mockResolvedValue({ count: 1, results: [snapshot(72)] })
    await wrapper.setProps({ session: { ...session, backup_source_snapshot_id: 71 } })
    await flushPromises()
    expect(mocks.listSnapshots).toHaveBeenLastCalledWith(expect.objectContaining({
      exclude_snapshot_id: 71, page: 1,
    }))
    expect(wrapper.get('select').element.value).toBe('')
    expect(wrapper.get('option[value="71"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('option[value="71"]').text()).toContain('Current')
    wrapper.unmount()
  })

  it('does not display stale applied metadata after switching Chats', async () => {
    let resolveOld!: (value: ReturnType<typeof snapshot>) => void
    mocks.getSnapshot.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(snapshot(80, { created_at: '2026-10-09T16:09:20+08:00' }))
    mocks.listSnapshots.mockResolvedValueOnce({ count: 1, results: [snapshot(71)] })
      .mockResolvedValueOnce({ count: 1, results: [snapshot(81, { created_at: '2026-10-10T16:09:20+08:00' })] })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await wrapper.setProps({ session: { ...session, id: 8, backup_source_snapshot_id: 80 } })
    await flushPromises()
    resolveOld(snapshot(70))
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('81')
    expect(wrapper.get('option[value="80"]').text()).toContain('Current')
    expect(wrapper.find('option[value="70"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('searches Snapshot ID server-side without losing the selected target', async () => {
    vi.useFakeTimers()
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    mocks.listSnapshots.mockResolvedValue({ count: 1, results: [snapshot(90)] })
    await wrapper.get('input[aria-label="Search Snapshot ID"]').setValue('bss-90')
    await vi.advanceTimersByTimeAsync(251)
    await flushPromises()
    expect(mocks.listSnapshots).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 1, snapshot_uid: 'bss-90', exclude_snapshot_id: 70,
    }))
    expect(wrapper.get('select').text()).toContain('bss-90')
    expect(wrapper.get('select').text()).toContain('bss-71')
    expect(wrapper.get('option[value="70"]').text()).toContain('Current')
    expect(mocks.getSnapshot).toHaveBeenCalledTimes(1)
    expect(wrapper.get('select').element.value).toBe('71')
    expect(wrapper.get('.chat-update-picker-footer').text()).toContain('1 / 1')
    mocks.listSnapshots.mockResolvedValue({ count: 0, results: [] })
    await wrapper.get('input[aria-label="Search Snapshot ID"]').setValue('no-match')
    await vi.advanceTimersByTimeAsync(251)
    await flushPromises()
    expect(wrapper.get('.chat-update-picker-footer').text()).toContain('0 / 0')
    expect(wrapper.find('.chat-update-hint').exists()).toBe(false)
    expect(wrapper.get('select').element.value).toBe('71')
    wrapper.unmount()
  })

  it('ignores a stale search response and cancels pending searches after closing', async () => {
    vi.useFakeTimers()
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    let resolveOld!: (value: { count: number; results: ReturnType<typeof snapshot>[] }) => void
    mocks.listSnapshots.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce({ count: 1, results: [snapshot(90)] })
    const input = wrapper.get('input[aria-label="Search Snapshot ID"]')
    await input.setValue('bss-80')
    await vi.advanceTimersByTimeAsync(251)
    await input.setValue('bss-90')
    await vi.advanceTimersByTimeAsync(251)
    resolveOld({ count: 1, results: [snapshot(80)] })
    await flushPromises()
    expect(wrapper.get('select').text()).toContain('bss-90')
    expect(wrapper.get('select').text()).not.toContain('bss-80')
    expect(wrapper.get('select').element.value).toBe('71')
    await input.setValue('pending-search')
    await wrapper.setProps({ modelValue: false })
    const count = mocks.listSnapshots.mock.calls.length
    await vi.advanceTimersByTimeAsync(251)
    expect(mocks.listSnapshots).toHaveBeenCalledTimes(count)
    wrapper.unmount()
  })

  it('retains the fixed retry ID if its display metadata is unavailable', async () => {
    mocks.getSnapshot.mockRejectedValue(new Error('Target unavailable'))
    const row = {
      ...session, data_update: { status: 'failed', target_snapshot_id: 999 },
    } as LensSessionLink
    const wrapper = mountDialog(row)
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('select').text()).toContain('#999')
    expect(wrapper.get('select').element.value).toBe('999')
    expect(wrapper.get('select').attributes('disabled')).toBeUndefined()
    expect(mocks.listSnapshots).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('retries a failed next page without skipping it or changing the selected target', async () => {
    mocks.listSnapshots.mockResolvedValueOnce({
      count: 31, results: Array.from({ length: 30 }, (_, index) => snapshot(100 - index)),
    }).mockRejectedValueOnce(new Error('Temporary failure')).mockResolvedValueOnce({
      count: 31, results: [snapshot(69)],
    })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('.chat-update-picker-footer').text()).toContain('30 / 31')
    await wrapper.get('.chat-update-picker-footer button').trigger('click')
    await flushPromises()
    expect(wrapper.get('.chat-update-picker-footer button').text()).toBe(en.common.retry)
    await wrapper.get('.chat-update-picker-footer button').trigger('click')
    await flushPromises()
    expect(mocks.listSnapshots.mock.calls.slice(1).map(([params]) => params.page)).toEqual([2, 2])
    expect(wrapper.get('.chat-update-picker-footer').text()).toContain('31 / 31')
    expect(wrapper.get('select').element.value).toBe('100')
    wrapper.unmount()
  })

  it('keeps snapshot loading errors visible without altering submission', async () => {
    mocks.listSnapshots.mockRejectedValue(new Error('Network unavailable'))
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.get('.chat-update-error[role="alert"]').text()).toBe(en.errors.generic.loadFailed)
    expect(mocks.update).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
