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

import { useAiModelForm } from './useAiModelForm'

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

  it('explicitly clears a custom API base when an active connection is verified', async () => {
    const modelForm = useAiModelForm(ref('model-uuid'))
    await modelForm.init()
    modelForm.form.api_base = ''
    modelForm.form.api_key = 'new-secret-value'

    await expect(modelForm.submit()).resolves.toBe(true)

    expect(mocks.updateLensModel).toHaveBeenCalledWith('model-uuid', {
      name: 'Existing model',
      provider: 'openai_compatible',
      config: {
        model: 'example/chat-model',
        api_base: '',
        api_key: 'new-secret-value',
      },
      is_active: true,
    })
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
})
