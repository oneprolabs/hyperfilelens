// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import CopilotShareDialog from './CopilotShareDialog.vue'
import type { SessionRow } from './sessionOrdering'

const mocks = vi.hoisted(() => ({
  fetchCandidate: vi.fn(),
  createShare: vi.fn(),
  updateShare: vi.fn(),
  revokeShare: vi.fn(),
}))

vi.mock('../../../lib/lensApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../lib/lensApi')>(),
  fetchCopilotShareCandidate: mocks.fetchCandidate,
  createCopilotShare: mocks.createShare,
  updateCopilotShare: mocks.updateShare,
  revokeCopilotShare: mocks.revokeShare,
}))

const DialogStub = defineComponent({
  props: { modelValue: Boolean },
  emits: ['update:modelValue', 'closed'],
  template: '<section><slot /><footer><slot name="footer" /></footer></section>',
})

const ButtonStub = defineComponent({
  emits: ['click'],
  template: '<button type="button" @click="$emit(\'click\')"><slot /></button>',
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

const InputStub = defineComponent({
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)">',
})

const DangerConfirmDialogStub = defineComponent({
  props: {
    modelValue: Boolean,
    confirmText: { type: String, default: '' },
  },
  emits: ['update:modelValue', 'confirm'],
  template: `
    <aside v-if="modelValue">
      <button class="confirm-stop-sharing" type="button" @click="$emit('confirm')">
        {{ confirmText }}
      </button>
    </aside>
  `,
})

function session(): SessionRow {
  return {
    id: 7,
    title: 'Quarterly review',
    lifecycle_status: 'ready',
    status: 'active',
    sl_session_uuid: '624164c3-fb99-4c9b-a5db-973b581b3d8d',
    sl_assistant_uuid: '0a381948-602a-4cb7-b57e-df41ef3fcb68',
    assistant_name: 'Backup Analyst',
    last_message_at: '2026-08-20T08:00:00Z',
    last_assistant_message_at: '2026-08-20T08:00:00Z',
    last_viewed_at: null,
    has_unread: false,
    pinned_at: null,
    created_at: '2026-08-20T07:00:00Z',
    updated_at: '2026-08-20T08:00:00Z',
    group: 'today',
  }
}

function mountDialog() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotShareDialog, {
    props: { modelValue: false, session: session() },
    global: {
      plugins: [i18n],
      directives: { loading: {} },
      stubs: {
        ElDialog: DialogStub,
        ElButton: ButtonStub,
        ElInput: InputStub,
        DangerConfirmDialog: DangerConfirmDialogStub,
        ElEmpty: defineComponent({
          props: { description: String },
          template: '<p>{{ description }}</p>',
        }),
      },
    },
  })
}

describe('CopilotShareDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads the latest completed SourceLens answer', async () => {
    mocks.fetchCandidate.mockResolvedValue({
      shareable: true,
      question: 'Summarize the latest backup.',
      answer: 'The backup completed successfully.',
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      share: null,
    })

    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(mocks.fetchCandidate).toHaveBeenCalledWith(7)
    expect(wrapper.text()).toContain('The backup completed successfully.')
    expect(wrapper.get('input').element.value).toBe('Summarize the latest backup.')
  })

  it('ignores a stale candidate after switching the selected Chat', async () => {
    let resolveFirst!: (value: {
      shareable: boolean
      question: string
      answer: string
      run_uuid: string
      share: null
    }) => void
    mocks.fetchCandidate
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveFirst = resolve
      }))
      .mockResolvedValueOnce({
        shareable: true,
        question: 'Question from the new Chat',
        answer: 'New answer',
        run_uuid: '1d22c6b6-0710-41f1-9496-fbdc3f81d32e',
        share: null,
      })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await wrapper.setProps({ session: { ...session(), id: 8 } })
    await flushPromises()

    expect(wrapper.get('input').element.value).toBe('Question from the new Chat')
    resolveFirst({
      shareable: true,
      question: 'Stale question',
      answer: 'Stale answer',
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      share: null,
    })
    await flushPromises()

    expect(wrapper.get('input').element.value).toBe('Question from the new Chat')
    wrapper.unmount()
  })

  it('creates a SourceLens share without copying the answer into HFL', async () => {
    mocks.fetchCandidate.mockResolvedValue({
      shareable: true,
      question: 'Summarize the latest backup.',
      answer: 'The backup completed successfully.',
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      share: null,
    })
    mocks.createShare.mockResolvedValue({
      uuid: 'a05bce34-1199-4a5e-8917-d61e541ca71b',
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      title: 'Summarize the latest backup.',
      share_path: '/insight/copilot/shared?access=signed',
    })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const createButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Create link')
    ))
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')
    await flushPromises()

    expect(mocks.createShare).toHaveBeenCalledWith(7, 'Summarize the latest backup.')
    expect(wrapper.text()).toContain('signed')
  })

  it('does not apply a completed share request to a newly selected Chat', async () => {
    const createRequest = deferred<{
      uuid: string
      run_uuid: string
      title: string
      share_path: string
    }>()
    mocks.fetchCandidate
      .mockResolvedValueOnce({
        shareable: true,
        question: 'Question from Chat A',
        answer: 'Answer from Chat A',
        run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
        share: null,
      })
      .mockResolvedValueOnce({
        shareable: true,
        question: 'Question from Chat B',
        answer: 'Answer from Chat B',
        run_uuid: '1d22c6b6-0710-41f1-9496-fbdc3f81d32e',
        share: null,
      })
    mocks.createShare.mockReturnValue(createRequest.promise)
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const createButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Create link')
    ))
    await createButton!.trigger('click')
    await wrapper.setProps({ session: { ...session(), id: 8 } })
    await flushPromises()
    expect(wrapper.get('input').element.value).toBe('Question from Chat B')

    createRequest.resolve({
      uuid: 'a05bce34-1199-4a5e-8917-d61e541ca71b',
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      title: 'Question from Chat A',
      share_path: '/insight/copilot/shared?access=chat-a-signed',
    })
    await flushPromises()

    expect(wrapper.get('input').element.value).toBe('Question from Chat B')
    expect(wrapper.text()).not.toContain('chat-a-signed')
    wrapper.unmount()
  })

  it('revokes the SourceLens share through the HFL cleanup adapter', async () => {
    mocks.fetchCandidate.mockResolvedValue({
      shareable: true,
      question: 'Question',
      answer: 'Answer',
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      share: {
        uuid: 'a05bce34-1199-4a5e-8917-d61e541ca71b',
        run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
        title: 'Shared answer',
        share_path: '/insight/copilot/shared?access=signed',
      },
    })
    mocks.revokeShare.mockResolvedValue(undefined)
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const stopButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Stop sharing')
    ))
    expect(stopButton).toBeTruthy()
    await stopButton!.trigger('click')
    await flushPromises()

    expect(mocks.revokeShare).not.toHaveBeenCalled()
    await wrapper.get('.confirm-stop-sharing').trigger('click')
    await flushPromises()

    expect(mocks.revokeShare).toHaveBeenCalledWith(
      7,
      'a05bce34-1199-4a5e-8917-d61e541ca71b',
    )
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })
})
