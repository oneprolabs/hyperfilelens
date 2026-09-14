// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import type { LensSessionLink } from '../../../lib/lensApi'
import CopilotExecutionSettingsDialog from './CopilotExecutionSettingsDialog.vue'

const mocks = vi.hoisted(() => ({
  patchExecution: vi.fn(),
}))

vi.mock('../../../lib/lensApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../lib/lensApi')>(),
  patchCopilotSessionExecution: mocks.patchExecution,
}))

const DialogStub = defineComponent({
  props: { modelValue: Boolean, width: String },
  emits: ['update:modelValue'],
  template: '<section><slot /><footer><slot name="footer" /></footer></section>',
})

const ButtonStub = defineComponent({
  emits: ['click'],
  template: '<button type="button" @click="$emit(\'click\')"><slot /></button>',
})

function session(overrides: Partial<LensSessionLink> = {}): LensSessionLink {
  return {
    id: 7,
    title: 'Backup Chat',
    lifecycle_status: 'ready',
    status: 'active',
    sl_session_uuid: '624164c3-fb99-4c9b-a5db-973b581b3d8d',
    sl_assistant_uuid: '0a381948-602a-4cb7-b57e-df41ef3fcb68',
    selected_task: null,
    analysis_type: null,
    analysis_mode: 'standard',
    agent_model_ref: null,
    knowledge_source: 3,
    knowledge_source_name: 'Backup data',
    backup_config_id: 4,
    backup_source_name: 'Backup source',
    backup_source_snapshot_id: 5,
    snapshot_created_at: null,
    snapshot_size_bytes: null,
    source_scopes_json: [],
    gateway_link: 6,
    gateway_selection_mode: 'auto',
    gateway_name: 'Public Data Gateway',
    gateway_scope: 'platform',
    ...overrides,
  } as LensSessionLink
}

function mountDialog(chat: LensSessionLink) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotExecutionSettingsDialog, {
    props: {
      modelValue: true,
      session: chat,
    },
    global: {
      plugins: [i18n],
      stubs: {
        ElDialog: DialogStub,
        ElButton: ButtonStub,
      },
    },
  })
}

describe('CopilotExecutionSettingsDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('treats a legacy Chat as Knowledge Q&A', () => {
    const wrapper = mountDialog(session())

    expect(wrapper.findComponent(DialogStub).props('width')).toBe('min(680px, calc(100vw - 32px))')
    expect((wrapper.get('input[value="knowledge_qa"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.text()).not.toContain(en.insight.copilot.analysisModeLabel)
    expect(wrapper.text()).not.toContain(en.insight.copilot.conversationModelLabel)
  })

  it('updates the SourceLens-backed analysis type through the HFL endpoint', async () => {
    const updated = session({ analysis_type: 'code_analysis' })
    mocks.patchExecution.mockResolvedValue(updated)
    const wrapper = mountDialog(session())

    await wrapper.get('input[value="code_analysis"]').setValue(true)
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await flushPromises()

    expect(mocks.patchExecution).toHaveBeenCalledWith(7, {
      analysis_type: 'code_analysis',
    })
    expect(wrapper.emitted('saved')?.[0]).toEqual([updated])
  })

  it('keeps both analysis types available for a legacy Chat', () => {
    const wrapper = mountDialog(session())

    expect(wrapper.get('input[value="knowledge_qa"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('input[value="code_analysis"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain(en.insight.copilot.analysisTypeKnowledgeQaHint)
    expect(wrapper.text()).toContain(en.insight.copilot.analysisTypeCodeAnalysisHint)
  })
})
