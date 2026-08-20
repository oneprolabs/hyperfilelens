// @vitest-environment jsdom

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElButton, ElInput, ElOption, ElRadioGroup, ElSelect } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import AddS3Repo from './AddS3Repo.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  fetchStorageProviderCatalog: vi.fn(),
  routerPush: vi.fn(),
}))

vi.mock('../../lib/api', () => ({
  api: mocks.api,
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

vi.mock('../../lib/storageProviderCatalogApi', () => ({
  fetchStorageProviderCatalog: mocks.fetchStorageProviderCatalog,
}))

vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRouter: () => ({ push: mocks.routerPush }),
}))

const catalog = {
  schema_version: 1,
  providers: [
    {
      id: 'huaweicloud',
      display_name: 'Huawei Cloud',
      enabled: true,
      regions: [
        {
          id: 'cn-north-1',
          display_name: 'CN North-Beijing1',
          region_group: 'asia_pacific',
          region_group_en: 'Asia Pacific',
          external_endpoint: 'obs.cn-north-1.example.com',
          internal_endpoint: 'obs.cn-north-1.example.com',
          driver: 's3',
          s3_url_style: 'virtual_hosted',
          use_tls: true,
        },
        {
          id: 'cn-north-4',
          display_name: 'CN North-Beijing4',
          region_group: 'asia_pacific',
          region_group_en: 'Asia Pacific',
          external_endpoint: 'obs.cn-north-4.example.com',
          internal_endpoint: 'obs.cn-north-4.example.com',
          driver: 's3',
          s3_url_style: 'virtual_hosted',
          use_tls: true,
        },
      ],
    },
  ],
}

async function mountForm() {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  const wrapper = mount(AddS3Repo, {
    global: { plugins: [ElementPlus, i18n] },
  })
  await flushPromises()

  const platformButton = wrapper
    .findAll('button.add-s3-platform-btn')
    .find((button) => button.text().includes('Huawei Cloud'))
  if (!platformButton) throw new Error('Huawei Cloud platform button was not rendered')
  await platformButton.trigger('click')
  await nextTick()

  return wrapper
}

function existingBucketSelect(wrapper: VueWrapper) {
  const select = wrapper
    .findAllComponents(ElSelect)
    .find((component) => component.classes().includes('add-s3-bucket-select'))
  if (!select) throw new Error('Existing Bucket select was not rendered')
  return select
}

describe('AddS3Repo Region and Bucket state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchStorageProviderCatalog.mockResolvedValue(catalog)
  })

  it('clears the selected Existing Bucket immediately when Region changes', async () => {
    const wrapper = await mountForm()
    const bucketSelect = existingBucketSelect(wrapper)

    bucketSelect.vm.$emit('update:modelValue', 'bucket-in-beijing1')
    await nextTick()
    expect(bucketSelect.props('modelValue')).toBe('bucket-in-beijing1')
    expect(wrapper.findAllComponents(ElOption).some(
      (option) => option.props('value') === 'bucket-in-beijing1',
    )).toBe(true)

    const regions = wrapper.findAll('button.add-s3-region-btn')
    await regions[1].trigger('click')
    await nextTick()

    expect(bucketSelect.props('modelValue')).toBe('')
    expect(wrapper.findAllComponents(ElOption).some(
      (option) => option.props('value') === 'bucket-in-beijing1',
    )).toBe(false)
  })

  it('keeps the selected Bucket when the active Region is clicked again', async () => {
    const wrapper = await mountForm()
    const bucketSelect = existingBucketSelect(wrapper)

    bucketSelect.vm.$emit('update:modelValue', 'bucket-in-beijing1')
    await nextTick()
    await wrapper.findAll('button.add-s3-region-btn')[0].trigger('click')
    await nextTick()

    expect(bucketSelect.props('modelValue')).toBe('bucket-in-beijing1')
  })

  it('clears credentials when the storage platform changes', async () => {
    const wrapper = await mountForm()
    const connectionInputs = wrapper
      .findAllComponents(ElInput)
      .filter((component) => component.classes().includes('add-s3-element-field'))
    connectionInputs[2].vm.$emit('update:modelValue', 'access-key')
    connectionInputs[3].vm.$emit('update:modelValue', 'secret-key')
    await nextTick()

    const otherPlatformButton = wrapper
      .findAll('button.add-s3-platform-btn')
      .find((button) => button.text().includes('S3-Compatible Storage'))
    if (!otherPlatformButton) throw new Error('S3-Compatible Storage platform was not rendered')
    await otherPlatformButton.trigger('click')
    await nextTick()

    expect(connectionInputs[2].props('modelValue')).toBe('')
    expect(connectionInputs[3].props('modelValue')).toBe('')
  })

  it('ignores a Bucket response from the previous Region', async () => {
    let resolveOldRegion: ((value: unknown) => void) | undefined
    mocks.api.mockReturnValue(new Promise((resolve) => {
      resolveOldRegion = resolve
    }))
    const wrapper = await mountForm()
    const connectionInputs = wrapper
      .findAllComponents(ElInput)
      .filter((component) => component.classes().includes('add-s3-element-field'))
    connectionInputs[2].vm.$emit('update:modelValue', 'access-key')
    connectionInputs[3].vm.$emit('update:modelValue', 'secret-key')
    await nextTick()

    const bucketSelect = existingBucketSelect(wrapper)
    bucketSelect.vm.$emit('visible-change', true)
    await nextTick()
    expect(mocks.api).toHaveBeenCalledOnce()

    await wrapper.findAll('button.add-s3-region-btn')[1].trigger('click')
    await nextTick()
    resolveOldRegion?.({ buckets: ['bucket-in-beijing1'] })
    await flushPromises()

    expect(wrapper.findAllComponents(ElOption).some(
      (option) => option.props('value') === 'bucket-in-beijing1',
    )).toBe(false)
  })

  it('preserves a New Bucket name when Region changes', async () => {
    const wrapper = await mountForm()
    const mode = wrapper
      .findAllComponents(ElRadioGroup)
      .find((component) => component.classes().includes('add-s3-bucket-segment'))
    if (!mode) throw new Error('Bucket mode selector was not rendered')
    mode.vm.$emit('update:modelValue', 'new')
    await nextTick()

    const bucketInput = wrapper
      .findAllComponents(ElInput)
      .filter((component) => component.classes().includes('add-s3-repo-primary-input'))[1]
    if (!bucketInput) throw new Error('New Bucket input was not rendered')
    bucketInput.vm.$emit('update:modelValue', 'new-bucket')
    await nextTick()
    await wrapper.findAll('button.add-s3-region-btn')[1].trigger('click')
    await nextTick()

    expect(bucketInput.props('modelValue')).toBe('new-bucket')
  })

  it('blocks an invalid managed New Bucket name with inline guidance', async () => {
    const wrapper = await mountForm()
    const connectionInputs = wrapper
      .findAllComponents(ElInput)
      .filter((component) => component.classes().includes('add-s3-element-field'))
    connectionInputs[2].vm.$emit('update:modelValue', 'access-key')
    connectionInputs[3].vm.$emit('update:modelValue', 'secret-key')
    const mode = wrapper
      .findAllComponents(ElRadioGroup)
      .find((component) => component.classes().includes('add-s3-bucket-segment'))
    if (!mode) throw new Error('Bucket mode selector was not rendered')
    mode.vm.$emit('update:modelValue', 'new')
    await nextTick()

    const bucketField = wrapper.find('[data-validation-field="bucket"]')
    const bucketInput = bucketField.findComponent(ElInput)
    bucketInput.vm.$emit('update:modelValue', 'Invalid_Bucket')
    await nextTick()

    const createButton = wrapper
      .findAllComponents(ElButton)
      .find((button) => button.text().includes('Create and Initialize'))
    if (!createButton) throw new Error('Create button was not rendered')
    await createButton.trigger('click')
    await nextTick()

    expect(bucketField.find('.el-form-item__error').text()).toContain('lowercase')
    expect(mocks.api).not.toHaveBeenCalled()
  })

  it('allows an Existing Bucket to use the Bucket root with an empty Prefix', async () => {
    mocks.api.mockImplementation((path: string) => {
      if (path.endsWith('/validate/s3/')) return Promise.resolve({ buckets: ['empty-bucket'] })
      if (path === '/api/v1/storage/repositories/') {
        return Promise.resolve({ id: 41, status: 'creating' })
      }
      return Promise.resolve({})
    })
    const wrapper = await mountForm()
    const connectionInputs = wrapper
      .findAllComponents(ElInput)
      .filter((component) => component.classes().includes('add-s3-element-field'))
    connectionInputs[2].vm.$emit('update:modelValue', 'access-key')
    connectionInputs[3].vm.$emit('update:modelValue', 'secret-key')
    existingBucketSelect(wrapper).vm.$emit('update:modelValue', 'empty-bucket')
    const prefixInput = wrapper
      .find('[data-validation-field="prefix"]')
      .findComponent(ElInput)
    prefixInput.vm.$emit('update:modelValue', '')
    await nextTick()

    const createButton = wrapper
      .findAllComponents(ElButton)
      .find((button) => button.text().includes('Create and Initialize'))
    if (!createButton) throw new Error('Create button was not rendered')
    await createButton.trigger('click')
    await flushPromises()

    const createCall = mocks.api.mock.calls.find(
      ([path]) => path === '/api/v1/storage/repositories/',
    )
    expect(createCall).toBeDefined()
    const payload = JSON.parse(String(createCall?.[1]?.body || '{}'))
    expect(payload.config.prefix).toBeUndefined()
    expect(wrapper.find('[data-validation-field="prefix"] .el-form-item__error').exists()).toBe(false)
  })
})
