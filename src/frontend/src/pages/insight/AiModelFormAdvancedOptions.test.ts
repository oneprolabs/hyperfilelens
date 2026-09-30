// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { computed, nextTick, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ useAiModelForm: vi.fn() }))

vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRoute: () => ({ params: {}, query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('vue-i18n', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-i18n')>(),
  useI18n: () => ({ t: (key: string) => key, locale: ref('en') }),
}))
vi.mock('../../lib/lensEngineRoutes', () => ({
  lensModelsPath: () => '/platform-ops/engine/ai-settings',
}))
vi.mock('../../composables/useAiModelForm', () => ({
  useAiModelForm: mocks.useAiModelForm,
  aiModelReferencePriceLine: () => '',
}))

import AiModelFormPage from './AiModelFormPage.vue'

function makeForm() {
  const form = reactive({
    provider: 'openai',
    model: '',
    name: '',
    api_base: '',
    api_key: '',
    advanced: {},
    is_active: true,
  })
  mocks.useAiModelForm.mockReturnValue({
    form,
    providers: ref([
      { id: 'openai', label: 'OpenAI', models: [] },
      { id: 'azure_openai', label: 'Azure OpenAI', models: [] },
    ]),
    advancedParameters: computed(() => [{
      name: form.provider === 'azure_openai' ? 'deployment' : 'temperature',
      label: 'Parameter',
      inputType: 'text',
      required: form.provider === 'azure_openai',
    }]),
    loading: ref(false),
    saving: ref(false),
    testing: ref(false),
    testOk: ref(null),
    testDetail: ref(''),
    testSummary: ref(null),
    apiKeyRequiredForSave: ref(false),
    modelDropdownOpen: ref(false),
    useCustomModel: ref(false),
    currentProviderModels: ref([]),
    apiBaseRequired: ref(false),
    selectedModelInfo: ref(null),
    hasSelectedModelInfo: ref(false),
    modelSelectLabel: ref(''),
    capabilityLabel: vi.fn(),
    capabilityClass: vi.fn(),
    selectModel: vi.fn(),
    onNameInput: vi.fn(),
    init: vi.fn().mockResolvedValue(undefined),
    runTest: vi.fn(),
    submit: vi.fn(),
    modelCapabilities: vi.fn().mockReturnValue([]),
    updateAdvancedParameter: vi.fn(),
  })
  return form
}

describe('AI Model Advanced Options', () => {
  it('stays collapsed for every provider and resets when the provider changes', async () => {
    const form = makeForm()
    const wrapper = mount(AiModelFormPage, {
      global: {
        stubs: {
          ElRadioGroup: { template: '<div><slot /></div>' },
          ElRadio: { template: '<div><slot /></div>' },
          ElForm: { template: '<div><slot /></div>' },
          ElFormItem: { template: '<div><slot /></div>' },
          ElInput: true,
          ElSwitch: true,
          ElCheckbox: true,
          ElButton: true,
          AiProviderIcon: true,
        },
        directives: { loading: {} },
      },
    })
    try {
      expect((wrapper.get('details.ai-model-advanced').element as HTMLDetailsElement).open).toBe(false)
      form.provider = 'azure_openai'
      await nextTick()
      expect((wrapper.get('details.ai-model-advanced').element as HTMLDetailsElement).open).toBe(false)
      wrapper.get('details.ai-model-advanced').element.open = true
      expect((wrapper.get('details.ai-model-advanced').element as HTMLDetailsElement).open).toBe(true)
      form.provider = 'openai'
      await nextTick()
      expect((wrapper.get('details.ai-model-advanced').element as HTMLDetailsElement).open).toBe(false)
      form.provider = 'azure_openai'
      await nextTick()
      expect((wrapper.get('details.ai-model-advanced').element as HTMLDetailsElement).open).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('stays collapsed when Azure OpenAI is the initial provider', () => {
    const form = makeForm()
    form.provider = 'azure_openai'
    const wrapper = mount(AiModelFormPage, {
      global: {
        stubs: {
          ElRadioGroup: { template: '<div><slot /></div>' },
          ElRadio: { template: '<div><slot /></div>' },
          ElForm: { template: '<div><slot /></div>' },
          ElFormItem: { template: '<div><slot /></div>' },
          ElInput: true,
          ElSwitch: true,
          ElCheckbox: true,
          ElButton: true,
          AiProviderIcon: true,
        },
        directives: { loading: {} },
      },
    })
    try {
      expect((wrapper.get('details.ai-model-advanced').element as HTMLDetailsElement).open).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})
