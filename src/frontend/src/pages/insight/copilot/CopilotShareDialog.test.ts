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
    expect(wrapper.get('section').classes()).toContain('hfl-flow-action-dialog')
    expect(wrapper.get('section').classes()).toContain('hfl-flow-action-dialog--form')
    expect(wrapper.text()).toContain('The backup completed successfully.')
    expect(wrapper.get('input').element.value).toBe('Summarize the latest backup.')
  })

  it('explains read-only organization access before creation and renders a Markdown preview', async () => {
    mocks.fetchCandidate.mockResolvedValue({
      shareable: true,
      run_uuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756',
      question: 'Summarize the backup.',
      answer: '## Backup Summary\n\n**Completed**\n\n- Seven files\n\n<script>alert(1)</script>',
      share: null,
    })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const intro = wrapper.get('.copilot-share-dialog__intro')
    expect(intro.find('.hfl-flow-action-dialog__lead-title').exists()).toBe(false)
    expect(intro.text()).toContain('Quarterly review')
    expect(intro.text()).not.toContain('Backup Analyst')
    expect(intro.text()).toContain(en.insight.copilot.shareOrgOnly)
    expect(wrapper.text().split(en.insight.copilot.shareOrgOnly)).toHaveLength(2)
    expect(wrapper.get('.copilot-share-dialog__notice').text()).toBe(en.insight.copilot.shareWarning)
    expect(wrapper.find('.copilot-share-dialog__warning').exists()).toBe(false)
    expect(wrapper.get('.copilot-share-dialog__notice').find('svg').exists()).toBe(false)
    expect(wrapper.find('.copilot-share-dialog__link-block').exists()).toBe(false)
    const preview = wrapper.get('.copilot-share-dialog__preview-content')
    expect(preview.attributes('tabindex')).toBe('0')
    expect(preview.get('h2').text()).toBe('Backup Summary')
    expect(preview.get('strong').text()).toBe('Completed')
    expect(preview.get('li').text()).toBe('Seven files')
    expect(preview.find('script').exists()).toBe(false)
    expect(wrapper.get('footer').text()).toContain('Create Link')
    wrapper.unmount()
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
      button.text().includes('Create Link')
    ))
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')
    await flushPromises()

    expect(mocks.createShare).toHaveBeenCalledWith(7, 'Summarize the latest backup.', '56ed8b87-b754-45d1-aaaf-e9134d52b756')
    expect(wrapper.text()).toContain('signed')
  })

  it('loads and creates the specific older answer rather than switching to the latest Run', async () => {
    const runUuid = '56ed8b87-b754-45d1-aaaf-e9134d52b756'
    mocks.fetchCandidate.mockResolvedValue({
      shareable: true, run_uuid: runUuid,
      question: 'Older question', answer: 'Older answer', share: null,
      shared_run_uuids: ['already-shared-other-run'],
    })
    mocks.createShare.mockResolvedValue({
      uuid: 'older-share', run_uuid: runUuid, title: 'Older question',
      share_path: '/insight/copilot/shared?access=older',
    })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true, runUuid })
    await flushPromises()
    expect(mocks.fetchCandidate).toHaveBeenCalledWith(7, runUuid)
    expect(wrapper.text()).toContain('Older answer')
    expect(wrapper.emitted('shareState')?.[0]).toEqual([expect.objectContaining({
      sessionId: 7, sharedRunUuids: ['already-shared-other-run'],
    })])
    await wrapper.findAll('button').find((button) => button.text().includes('Create Link'))!.trigger('click')
    await flushPromises()
    expect(mocks.createShare).toHaveBeenCalledWith(7, 'Older question', runUuid)
    wrapper.unmount()
  })

  it('ignores an older candidate request when switching answers in the same Chat', async () => {
    const first = deferred<Record<string, unknown>>()
    mocks.fetchCandidate.mockReturnValueOnce(first.promise).mockResolvedValueOnce({
      shareable: true, run_uuid: 'new-run',
      question: 'New question', answer: 'New answer', share: null,
    })
    const wrapper = mountDialog()
    await wrapper.setProps({ modelValue: true, runUuid: 'old-run' })
    await wrapper.setProps({ runUuid: 'new-run' })
    await flushPromises()
    first.resolve({
      shareable: true, run_uuid: 'old-run',
      question: 'Old question', answer: 'Old answer', share: null,
    })
    await flushPromises()
    expect(wrapper.get('input').element.value).toBe('New question')
    expect(wrapper.text()).not.toContain('Old answer')
    wrapper.unmount()
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
      button.text().includes('Create Link')
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
      button.text().includes('Stop Sharing')
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
    expect(wrapper.emitted('shareState')?.at(-1)).toEqual([expect.objectContaining({
      sessionId: 7, runUuid: '56ed8b87-b754-45d1-aaaf-e9134d52b756', isShared: false,
    })])
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })

  it('keeps sharing prominent and destructive actions in the standard footer order', async () => {
    const originalShare = navigator.share
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: vi.fn(),
    })
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

    try {
      const wrapper = mountDialog()
      await wrapper.setProps({ modelValue: true })
      await flushPromises()

      const shareButton = wrapper.get('.copilot-share-dialog__native-share-action')
      expect(shareButton.text()).toBe('Share Link')
      expect(shareButton.classes()).toContain('hfl-btn-with-icon')
      await shareButton.trigger('click')
      await flushPromises()
      expect(navigator.share).toHaveBeenCalledWith(expect.objectContaining({
        title: 'Shared answer',
        url: expect.stringContaining('/insight/copilot/shared?access=signed'),
      }))
      expect(wrapper.get('footer').findAll('button').map((button) => button.text().trim())).toEqual([
        'Stop Sharing',
        'Done',
      ])
      wrapper.unmount()
    } finally {
      Object.defineProperty(navigator, 'share', {
        configurable: true,
        value: originalShare,
      })
    }
  })
})
