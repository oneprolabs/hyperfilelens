// @vitest-environment jsdom

import { defineComponent, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import BackupSourceDeleteDialog from './BackupSourceDeleteDialog.vue'
import BackupSourceStep3DeleteDialog from './BackupSourceStep3DeleteDialog.vue'
import type { BackupSourceDeletePreflight } from '../lib/sourceApi'

const { preflightDeleteBackupSources, bulkDeleteBackupSources } = vi.hoisted(() => ({
  preflightDeleteBackupSources: vi.fn(),
  bulkDeleteBackupSources: vi.fn(),
}))

vi.mock('../lib/sourceApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/sourceApi')>()
  return {
    ...actual,
    preflightDeleteBackupSources,
    bulkDeleteBackupSources,
  }
})

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en },
})

const ElDialogStub = defineComponent({
  props: { modelValue: Boolean },
  emits: ['update:modelValue', 'close'],
  template: `
    <section v-if="modelValue" data-test="dialog">
      <slot />
      <slot name="footer" />
    </section>
  `,
})

const ElButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean, type: String },
  emits: ['click'],
  template: `
    <button
      :data-test="type === 'danger' ? 'confirm-delete' : 'dialog-button'"
      :disabled="disabled"
      @click="$emit('click')"
    >
      <slot />
    </button>
  `,
})

const BodyStub = defineComponent({
  name: 'BackupSourceUnregisterDialogBody',
  props: {
    force: Boolean,
    confirmText: String,
    preflight: Object,
    preflightLoading: Boolean,
    preflightError: Boolean,
    sourceIds: Array,
  },
  emits: ['update:force', 'update:confirmText', 'retry-preflight', 'confirm'],
  template: `
    <div data-test="body">
      <span data-test="preflight-loading">{{ preflightLoading }}</span>
      <span data-test="preflight-error">{{ preflightError }}</span>
      <span data-test="preflight-disabled">{{ preflight?.delete_disabled }}</span>
      <span data-test="dialog-source-count">{{ sourceIds?.length || 0 }}</span>
      <button data-test="select-force" @click="$emit('update:force', true)">force</button>
      <button data-test="strict-confirmation" @click="$emit('update:confirmText', 'DEREGISTER')">strict</button>
      <button data-test="force-confirmation" @click="$emit('update:confirmText', 'FORCE DEREGISTER')">force confirm</button>
      <button data-test="retry-preflight" @click="$emit('retry-preflight')">retry</button>
    </div>
  `,
})

type DialogComponent = typeof BackupSourceDeleteDialog | typeof BackupSourceStep3DeleteDialog

function mountDialog(component: DialogComponent, sourceIds = ['agent:25']) {
  return mount(component, {
    props: {
      modelValue: true,
      sourceIds,
    },
    global: {
      plugins: [i18n],
      stubs: {
        ElDialog: ElDialogStub,
        ElButton: ElButtonStub,
        BackupSourceUnregisterDialogBody: BodyStub,
      },
    },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

const successfulPreflight: BackupSourceDeletePreflight = {
  risks: [],
  blocking: [],
  strict_may_fail: false,
  delete_disabled: false,
}

describe.each([
  ['standard unregister dialog', BackupSourceDeleteDialog],
  ['step 3 unregister dialog', BackupSourceStep3DeleteDialog],
] as const)('%s', (_name, component) => {
  beforeEach(() => {
    preflightDeleteBackupSources.mockReset()
    bulkDeleteBackupSources.mockReset()
  })

  it('runs preflight immediately when dynamically mounted open', async () => {
    const request = deferred<typeof successfulPreflight>()
    preflightDeleteBackupSources.mockReturnValueOnce(request.promise)
    const wrapper = mountDialog(component)
    await nextTick()

    expect(preflightDeleteBackupSources).toHaveBeenCalledOnce()
    expect(preflightDeleteBackupSources).toHaveBeenCalledWith(['agent:25'], false)
    expect(wrapper.get('[data-test="preflight-loading"]').text()).toBe('true')
    expect(wrapper.get('[data-test="confirm-delete"]').attributes('disabled')).toBeDefined()

    request.resolve(successfulPreflight)
    await flushPromises()
    expect(wrapper.get('[data-test="preflight-loading"]').text()).toBe('false')
    wrapper.unmount()
  })

  it('keeps submit fail-closed when preflight fails and supports retry', async () => {
    preflightDeleteBackupSources
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce(successfulPreflight)
    const wrapper = mountDialog(component)
    await flushPromises()

    expect(wrapper.get('[data-test="preflight-error"]').text()).toBe('true')
    expect(wrapper.get('[data-test="confirm-delete"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="retry-preflight"]').trigger('click')
    await flushPromises()
    expect(preflightDeleteBackupSources).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-test="preflight-error"]').text()).toBe('false')
    wrapper.unmount()
  })

  it('ignores a stale preflight response after the selected source changes', async () => {
    const firstRequest = deferred<typeof successfulPreflight>()
    const secondRequest = deferred<typeof successfulPreflight>()
    preflightDeleteBackupSources
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise)
    const wrapper = mountDialog(component, ['agent:25'])
    await nextTick()

    await wrapper.setProps({ sourceIds: ['nas:31'] })
    await nextTick()
    secondRequest.resolve(successfulPreflight)
    await flushPromises()
    expect(wrapper.get('[data-test="preflight-disabled"]').text()).toBe('false')

    firstRequest.resolve({
      risks: [],
      blocking: [{ code: 'running_tasks', detail: 'Stale blocker.' }],
      strict_may_fail: false,
      delete_disabled: true,
    })
    await flushPromises()
    expect(wrapper.get('[data-test="preflight-disabled"]').text()).toBe('false')
    expect(preflightDeleteBackupSources).toHaveBeenNthCalledWith(2, ['nas:31'], false)
    wrapper.unmount()
  })

  it('lets the user select Force independently and submits the force contract', async () => {
    preflightDeleteBackupSources.mockResolvedValue(successfulPreflight)
    bulkDeleteBackupSources.mockResolvedValue({
      result: 'pending',
      warnings: [],
      pending_removals: [],
      deleted: [],
      ok: true,
      accepted: true,
    })
    const wrapper = mountDialog(component)
    await flushPromises()

    await wrapper.get('[data-test="select-force"]').trigger('click')
    await nextTick()
    await wrapper.get('[data-test="force-confirmation"]').trigger('click')
    await nextTick()
    expect(wrapper.get('[data-test="confirm-delete"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-test="confirm-delete"]').trigger('click')
    await flushPromises()
    expect(bulkDeleteBackupSources).toHaveBeenCalledWith(
      ['agent:25'],
      true,
      'FORCE DEREGISTER',
      expect.stringMatching(/^source-unregister:/),
    )
    wrapper.unmount()
  })

  it('keeps the submitted selection visible while the request is pending', async () => {
    const request = deferred<Record<string, unknown>>()
    preflightDeleteBackupSources.mockResolvedValue(successfulPreflight)
    bulkDeleteBackupSources.mockReturnValue(request.promise)
    const wrapper = mountDialog(component)
    await flushPromises()

    await wrapper.get('[data-test="strict-confirmation"]').trigger('click')
    await wrapper.get('[data-test="confirm-delete"]').trigger('click')
    await wrapper.setProps({ sourceIds: [] })
    await nextTick()

    expect(wrapper.get('[data-test="dialog-source-count"]').text()).toBe('1')
    expect(wrapper.emitted('started')).toBeUndefined()
    request.resolve({
      result: 'pending',
      warnings: [],
      pending_removals: [],
      deleted: [],
      ok: true,
      accepted: true,
    })
    await flushPromises()
    wrapper.unmount()
  })

  it('clears the Strict confirmation when switching to Force', async () => {
    preflightDeleteBackupSources.mockResolvedValue(successfulPreflight)
    const wrapper = mountDialog(component)
    await flushPromises()

    await wrapper.get('[data-test="strict-confirmation"]').trigger('click')
    await nextTick()
    expect(wrapper.get('[data-test="confirm-delete"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-test="select-force"]').trigger('click')
    await nextTick()
    expect(wrapper.get('[data-test="confirm-delete"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('does not let Force bypass a blocking preflight', async () => {
    preflightDeleteBackupSources.mockResolvedValue({
      risks: [],
      blocking: [{ code: 'source_not_found', detail: 'Source does not exist.' }],
      strict_may_fail: false,
      delete_disabled: true,
    })
    const wrapper = mountDialog(component)
    await flushPromises()

    await wrapper.get('[data-test="select-force"]').trigger('click')
    await wrapper.get('[data-test="force-confirmation"]').trigger('click')
    await nextTick()
    expect(wrapper.get('[data-test="confirm-delete"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="confirm-delete"]').trigger('click')
    expect(bulkDeleteBackupSources).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('allows a waiting request without emitting a local failure state', async () => {
    preflightDeleteBackupSources.mockResolvedValue({
      risks: [],
      waiting: [{ code: 'running_tasks', detail: 'Backup is running.' }],
      blocking: [],
      strict_may_fail: false,
      delete_disabled: false,
    })
    bulkDeleteBackupSources.mockResolvedValue({
      result: 'pending',
      warnings: [],
      pending_removals: [],
      deleted: [],
      ok: true,
      accepted: true,
      status: 'waiting',
      rejected: [{
        source_id: 'nas:999',
        reasons: [{ code: 'source_not_found', detail: 'Source does not exist.' }],
      }],
    })
    const wrapper = mountDialog(component)
    await flushPromises()

    await wrapper.get('[data-test="strict-confirmation"]').trigger('click')
    await wrapper.get('[data-test="confirm-delete"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('started')).toBeUndefined()
    expect(wrapper.emitted('failed')).toBeUndefined()
    expect(wrapper.emitted('deleted')).toHaveLength(1)
    expect(wrapper.emitted('deleted')?.[0]?.[0]).toMatchObject({
      rejected: [{ source_id: 'nas:999' }],
    })
    wrapper.unmount()
  })
})
