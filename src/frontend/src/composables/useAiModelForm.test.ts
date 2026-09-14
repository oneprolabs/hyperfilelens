// @vitest-environment jsdom

import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createLensModel: vi.fn(),
  fetchLensModelCatalog: vi.fn(),
  fetchLensModelDetail: vi.fn(),
  fetchLensModelProviders: vi.fn(),
  testSavedLensModel: vi.fn(),
  testLensModel: vi.fn(),
  updateLensModel: vi.fn(),
  messageSuccess: vi.fn(),
  messageWarning: vi.fn(),
  messageError: vi.fn(),
  openErrorDetails: vi.fn(),
  apiErrorMessageI18n: vi.fn(),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'insight.aiSettings.connectionTestSuccessDescription') {
        return `HFL reached ${params?.provider} and received a valid response from ${params?.model}.`
      }
      return key
    },
  }),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    success: mocks.messageSuccess,
    warning: mocks.messageWarning,
    error: mocks.messageError,
  },
}))

vi.mock('../lib/errors/details', () => ({
  openErrorDetails: mocks.openErrorDetails,
}))

vi.mock('../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
  apiErrorMessageI18n: mocks.apiErrorMessageI18n,
}))

vi.mock('../lib/lensApi', () => ({
  createLensModel: mocks.createLensModel,
  fetchLensModelCatalog: mocks.fetchLensModelCatalog,
  fetchLensModelDetail: mocks.fetchLensModelDetail,
  fetchLensModelProviders: mocks.fetchLensModelProviders,
  testSavedLensModel: mocks.testSavedLensModel,
  testLensModel: mocks.testLensModel,
  updateLensModel: mocks.updateLensModel,
}))

import { aiModelReferencePriceLine, useAiModelForm } from './useAiModelForm'

describe('useAiModelForm SourceLens metadata', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchLensModelCatalog.mockResolvedValue({
      providers: [{
        id: 'openai',
        label: 'OpenAI',
        default_api_base: 'https://api.openai.com/v1',
        models: [{
          id: 'gpt-test',
          capabilities: ['text-to-text'],
          max_input_tokens: 128_000,
          max_output_tokens: 16_384,
          reference_pricing: {
            input_usd_per_1m: 2.5,
            output_usd_per_1m: 10,
          },
        }],
      }, {
        id: 'azure_openai',
        label: 'Azure OpenAI',
        models: [{ id: 'azure-model' }],
      }],
      capability_labels: { 'text-to-text': 'Text' },
    })
    mocks.fetchLensModelProviders.mockResolvedValue({
      providers: {
        openai: {
          editable_params: [
            'api_base',
            'api_key',
            'model',
            'max_tokens',
            'temperature',
            'top_p',
            'request_timeout_seconds',
            'num_retries',
          ],
          default_max_tokens: 16_384,
          default_num_retries: 3,
          default_temperature: 0.7,
          default_top_p: 1,
          default_api_base: 'https://api.openai.com/v1',
        },
        azure_openai: {
          required: ['api_key', 'api_base', 'deployment'],
          editable_params: [
            'api_base',
            'api_key',
            'deployment',
            'model',
            'api_version',
          ],
        },
      },
    })
    mocks.createLensModel.mockResolvedValue({ uuid: 'model-uuid' })
  })

  it('uses catalog metadata and provider schema without maintaining a second model definition', async () => {
    const modelForm = useAiModelForm(ref(null))
    await modelForm.init()
    modelForm.selectModel('gpt-test')

    expect(modelForm.selectedModelInfo.value).toEqual({
      capabilities: ['text-to-text'],
      max_input_tokens: 128_000,
      max_output_tokens: 16_384,
      reference_pricing: {
        input_usd_per_1m: 2.5,
        output_usd_per_1m: 10,
      },
    })
    expect(modelForm.hasSelectedModelInfo.value).toBe(true)
    expect(modelForm.advancedParameters.value.map(({ name }) => name)).toEqual([
      'max_tokens',
      'temperature',
      'top_p',
      'request_timeout_seconds',
      'num_retries',
    ])
    expect(modelForm.advancedParameters.value[0]?.defaultValue).toBe(16_384)
    expect(aiModelReferencePriceLine(
      modelForm.selectedModelInfo.value?.reference_pricing,
      { input: 'input', output: 'output' },
    )).toBe(
      '$2.50/1M input · $10/1M output',
    )
    expect(aiModelReferencePriceLine(
      modelForm.selectedModelInfo.value?.reference_pricing,
      { input: 'entrada', output: 'salida' },
      'es',
    )).toBe('$2,50/1M entrada · $10/1M salida')
  })

  it('sends configured advanced parameters through the existing model API', async () => {
    const modelForm = useAiModelForm(ref(null))
    await modelForm.init()
    modelForm.selectModel('gpt-test')
    modelForm.form.name = 'OpenAI · Test'
    modelForm.onNameInput()
    modelForm.form.api_key = 'secret-value'
    modelForm.updateAdvancedParameter('max_tokens', 4096)
    modelForm.updateAdvancedParameter('temperature', 0)
    modelForm.updateAdvancedParameter('num_retries', 0)

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.createLensModel).toHaveBeenCalledWith({
      name: 'OpenAI · Test',
      provider: 'openai',
      config: {
        model: 'gpt-test',
        api_base: 'https://api.openai.com/v1',
        api_key: 'secret-value',
        max_tokens: 4096,
        num_retries: 0,
        temperature: 0,
      },
      is_active: true,
    })
  })

  it('clears the previous endpoint and exposes required provider fields when provider changes', async () => {
    const modelForm = useAiModelForm(ref(null))
    await modelForm.init()
    expect(modelForm.form.api_base).toBe('https://api.openai.com/v1')

    modelForm.form.provider = 'azure_openai'
    await nextTick()

    expect(modelForm.form.api_base).toBe('')
    expect(modelForm.apiBaseRequired.value).toBe(true)
    expect(modelForm.advancedParameters.value).toEqual([
      expect.objectContaining({ name: 'deployment', required: true, inputType: 'text' }),
      expect.objectContaining({ name: 'api_version', required: false, inputType: 'text' }),
    ])
  })

  it('keeps OpenAI-compatible requirements when the provider schema omits that provider', async () => {
    const modelForm = useAiModelForm(ref(null))
    await modelForm.init()

    modelForm.form.provider = 'openai_compatible'
    await nextTick()

    expect(modelForm.apiBaseRequired.value).toBe(true)
    expect(modelForm.advancedParameters.value).toEqual([
      expect.objectContaining({ name: 'max_tokens', inputType: 'number' }),
      expect.objectContaining({ name: 'temperature', inputType: 'number' }),
      expect.objectContaining({ name: 'top_p', inputType: 'number' }),
      expect.objectContaining({ name: 'request_timeout_seconds', inputType: 'number' }),
      expect.objectContaining({
        name: 'num_retries',
        inputType: 'number',
        min: 0,
        max: 10,
        step: 1,
        integer: true,
      }),
      expect.objectContaining({ name: 'vision', inputType: 'boolean' }),
    ])

    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    await expect(modelForm.submit()).resolves.toBe(false)
    expect(mocks.messageWarning).toHaveBeenCalledWith({
      message: 'insight.aiSettings.requiredProviderField',
      grouping: true,
    })
    expect(mocks.createLensModel).not.toHaveBeenCalled()
  })

  it('saves confirmed vision support for an OpenAI-compatible model', async () => {
    const modelForm = useAiModelForm(ref(null))
    await modelForm.init()
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.name = 'Vision model'
    modelForm.form.model = 'example/vision-model'
    modelForm.form.api_base = 'https://models.example.test/v1'
    modelForm.form.api_key = 'secret-value'
    modelForm.updateAdvancedParameter('vision', true)

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.createLensModel).toHaveBeenCalledWith(expect.objectContaining({
      config: expect.objectContaining({ vision: true }),
    }))
  })

  it.each([-1, 1.5, 11])('rejects an invalid retry count of %s', async (value) => {
    const modelForm = useAiModelForm(ref(null))
    await modelForm.init()
    modelForm.selectModel('gpt-test')
    modelForm.form.api_key = 'secret-value'
    modelForm.updateAdvancedParameter('num_retries', value)

    await expect(modelForm.submit()).resolves.toBe(false)
    expect(mocks.messageWarning).toHaveBeenCalledWith({
      message: 'insight.aiSettings.invalidProviderField',
      grouping: true,
    })
    expect(mocks.createLensModel).not.toHaveBeenCalled()
  })
})

describe('useAiModelForm connection testing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.apiErrorMessageI18n.mockReturnValue('localized connection failure')
  })

  it('shows a useful summary for an explicitly successful test', async () => {
    mocks.testLensModel.mockResolvedValue({ ok: true, message: 'OK' })
    const modelForm = useAiModelForm(ref(null))
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    modelForm.form.api_base = 'https://models.example.test/v1'
    await nextTick()

    await expect(modelForm.runTest()).resolves.toBe(true)

    expect(modelForm.testOk.value).toBe(true)
    expect(modelForm.testDetail.value).toBe(
      'HFL reached openai_compatible and received a valid response from example/chat-model.',
    )
    expect(modelForm.testSummary.value).toEqual({
      provider: 'openai_compatible',
      model: 'example/chat-model',
      endpoint: 'https://models.example.test/v1',
      durationMs: expect.any(Number),
    })
    expect(mocks.messageSuccess).toHaveBeenCalledOnce()
  })

  it('does not treat an ambiguous response as a successful test', async () => {
    mocks.testLensModel.mockResolvedValue({ message: 'OK' })
    const modelForm = useAiModelForm(ref(null))
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    modelForm.form.api_base = 'https://models.example.test/v1'
    await nextTick()

    await expect(modelForm.runTest()).resolves.toBe(false)

    expect(modelForm.testOk.value).toBe(false)
    expect(mocks.openErrorDetails).toHaveBeenCalledOnce()
    expect(mocks.messageSuccess).not.toHaveBeenCalled()
  })

  it('localizes a connection request failure', async () => {
    const error = { errorCode: 'NETWORK.UNAVAILABLE' }
    mocks.testLensModel.mockRejectedValue(error)
    const modelForm = useAiModelForm(ref(null))
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    modelForm.form.api_base = 'https://models.example.test/v1'
    await nextTick()

    await expect(modelForm.runTest()).resolves.toBe(false)

    expect(mocks.apiErrorMessageI18n).toHaveBeenCalledWith(
      error,
      expect.any(Function),
      'insight.aiSettings.connectionTestFailureFallback',
    )
    expect(modelForm.testDetail.value).toBe('localized connection failure')
    expect(mocks.openErrorDetails).toHaveBeenCalledOnce()
  })

  it('clears a successful result when connection settings change', async () => {
    mocks.testLensModel.mockResolvedValue({ success: true })
    const modelForm = useAiModelForm(ref(null))
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    modelForm.form.api_base = 'https://models.example.test/v1'
    await nextTick()

    await modelForm.runTest()
    expect(modelForm.testOk.value).toBe(true)

    modelForm.form.model = 'example/other-model'
    await nextTick()

    expect(modelForm.testOk.value).toBeNull()
    expect(modelForm.testDetail.value).toBe('')
    expect(modelForm.testSummary.value).toBeNull()
  })

  it('does not restore a stale result when settings change during a test', async () => {
    let resolveTest: (value: unknown) => void = () => undefined
    mocks.testLensModel.mockImplementation(
      () => new Promise((resolve) => {
        resolveTest = resolve
      }),
    )
    const modelForm = useAiModelForm(ref(null))
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    modelForm.form.api_base = 'https://models.example.test/v1'
    await nextTick()

    const pendingTest = modelForm.runTest()
    modelForm.form.model = 'example/other-model'
    await nextTick()
    resolveTest({ ok: true })

    await expect(pendingTest).resolves.toBe(false)
    expect(modelForm.testOk.value).toBeNull()
    expect(modelForm.testDetail.value).toBe('')
    expect(modelForm.testSummary.value).toBeNull()
    expect(mocks.messageSuccess).not.toHaveBeenCalled()
  })

  it('uses the localized stable error when saving an active model fails validation', async () => {
    const error = { errorCode: 'AI_MODEL.CONNECTION_TEST_FAILED' }
    mocks.createLensModel.mockRejectedValue(error)
    const modelForm = useAiModelForm(ref(null))
    modelForm.form.provider = 'openai_compatible'
    await nextTick()
    modelForm.form.name = 'Example model'
    modelForm.form.model = 'example/chat-model'
    modelForm.form.api_key = 'secret-value'
    modelForm.form.api_base = 'https://models.example.test/v1'

    await expect(modelForm.submit()).resolves.toBe(false)

    expect(modelForm.apiKeyRequiredForSave.value).toBe(true)
    expect(mocks.apiErrorMessageI18n).toHaveBeenCalledWith(
      error,
      expect.any(Function),
      'errors.generic.requestFailed',
    )
    expect(mocks.messageError).toHaveBeenCalledWith({
      message: 'localized connection failure',
      grouping: true,
    })
  })
})

describe('useAiModelForm model updates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchLensModelCatalog.mockResolvedValue({ providers: [] })
    mocks.fetchLensModelProviders.mockResolvedValue({ providers: {} })
    mocks.fetchLensModelDetail.mockResolvedValue({
      uuid: 'model-uuid',
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
      },
      is_active: true,
    })
    mocks.updateLensModel.mockResolvedValue({ uuid: 'model-uuid' })
  })

  it('updates only the display name without requiring or testing a hidden API key', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.name = 'Renamed model'

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Renamed model',
    })
    expect(mocks.testLensModel).not.toHaveBeenCalled()
  })

  it('tests an unchanged active model with its saved configuration', async () => {
    mocks.testSavedLensModel.mockResolvedValue({ ok: true })
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()

    await expect(modelForm.runTest()).resolves.toBe(true)

    expect(mocks.testSavedLensModel).toHaveBeenCalledWith('model-uuid')
    expect(mocks.testLensModel).not.toHaveBeenCalled()
  })

  it('requires the API key again before changing an active connection', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.model = 'example/other-model'

    await expect(modelForm.submit()).resolves.toBe(false)

    expect(mocks.messageWarning).toHaveBeenCalledWith({
      message: 'insight.aiSettings.apiKeyRequiredForConnectionChange',
      grouping: true,
    })
    expect(mocks.updateLensModel).not.toHaveBeenCalled()
  })

  it('sends complete settings when an active connection is changed with a new key', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.model = 'example/other-model'
    modelForm.form.api_key = 'new-secret-value'

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/other-model',
        api_base: 'https://models.example.test/v1',
        api_key: 'new-secret-value',
      },
      is_active: true,
    })
  })

  it('rejects clearing the required API base for an OpenAI-compatible model', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.api_base = ''
    modelForm.form.api_key = 'new-secret-value'

    await expect(modelForm.submit()).resolves.toBe(false)

    expect(mocks.messageWarning).toHaveBeenCalledWith({
      message: 'insight.aiSettings.requiredProviderField',
      grouping: true,
    })
    expect(mocks.updateLensModel).not.toHaveBeenCalled()
  })

  it('sends a replacement API key even when other connection settings are unchanged', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.api_key = 'replacement-secret-value'

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
        api_key: 'replacement-secret-value',
      },
      is_active: true,
    })
  })

  it('allows connection settings to be saved without testing when disabling a model', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.model = 'example/other-model'
    modelForm.form.is_active = false

    expect(modelForm.apiKeyRequiredForSave.value).toBe(false)
    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/other-model',
        api_base: 'https://models.example.test/v1',
      },
      is_active: false,
    })
  })

  it('requires the API key again before enabling an inactive model', async () => {
    mocks.fetchLensModelDetail.mockResolvedValue({
      uuid: 'model-uuid',
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
      },
      is_active: false,
    })
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.is_active = true

    expect(modelForm.apiKeyRequiredForSave.value).toBe(true)
    await expect(modelForm.submit()).resolves.toBe(false)

    expect(mocks.messageWarning).toHaveBeenCalledWith({
      message: 'insight.aiSettings.apiKeyRequiredForActivation',
      grouping: true,
    })
    expect(mocks.updateLensModel).not.toHaveBeenCalled()
  })

  it('sends complete settings when enabling an inactive model with a re-entered key', async () => {
    mocks.fetchLensModelDetail.mockResolvedValue({
      uuid: 'model-uuid',
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
      },
      is_active: false,
    })
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.is_active = true
    modelForm.form.api_key = 'reentered-secret-value'

    expect(modelForm.apiKeyRequiredForSave.value).toBe(true)
    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
        api_key: 'reentered-secret-value',
      },
      is_active: true,
    })
  })

  it('saves changed settings on an inactive model without requiring the key again', async () => {
    mocks.fetchLensModelDetail.mockResolvedValue({
      uuid: 'model-uuid',
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
      },
      is_active: false,
    })
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.model = 'example/other-model'

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(modelForm.form.api_key).toBe('')
    expect(mocks.messageWarning).not.toHaveBeenCalled()
    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/other-model',
        api_base: 'https://models.example.test/v1',
      },
      is_active: false,
    })
  })

  it('loads, changes, and explicitly clears SourceLens advanced parameters', async () => {
    mocks.fetchLensModelProviders.mockResolvedValue({
      providers: {
        openai_compatible: {
          editable_params: [
            'api_base',
            'api_key',
            'model',
            'max_tokens',
            'temperature',
            'vision',
          ],
        },
      },
    })
    mocks.fetchLensModelDetail.mockResolvedValue({
      uuid: 'model-uuid',
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
        max_tokens: 2048,
        temperature: 0.4,
        vision: true,
      },
      is_active: true,
    })
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()

    expect(modelForm.form.advanced).toMatchObject({
      max_tokens: 2048,
      temperature: 0.4,
      vision: true,
    })
    modelForm.updateAdvancedParameter('max_tokens', undefined)
    modelForm.updateAdvancedParameter('temperature', 0.2)
    modelForm.updateAdvancedParameter('vision', false)
    modelForm.form.api_key = 'new-secret-value'

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: 'https://models.example.test/v1',
        api_key: 'new-secret-value',
        max_tokens: null,
        temperature: 0.2,
        vision: false,
      },
      is_active: true,
    })
  })
})
