// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElInputNumber, ElMessage } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import EditS3Repo from './EditS3Repo.vue'

const mocks = vi.hoisted(() => ({
  getStorageRepository: vi.fn(),
  updateStorageRepository: vi.fn(),
  verifyStorageRepositoryAccess: vi.fn(),
  routerPush: vi.fn(),
}))

vi.mock('../../lib/storageRepositoryApi', () => ({
  getStorageRepository: mocks.getStorageRepository,
  updateStorageRepository: mocks.updateStorageRepository,
  verifyStorageRepositoryAccess: mocks.verifyStorageRepositoryAccess,
}))

vi.mock('../../lib/api', () => ({
  apiErrorMessage: (error: { message?: string }, fallback: string) => error?.message || fallback,
}))

vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRoute: () => ({ params: { id: '42' } }),
  useRouter: () => ({ push: mocks.routerPush, replace: vi.fn() }),
}))

const repository = {
  id: 42,
  organization_id: 1,
  name: 'Object storage',
  repo_type: 's3',
  status: 'created',
  health: 'online',
  s3_platform: 'custom',
  s3_bucket: 'backups',
  capacity_bytes: 0,
  estimated_usage_bytes: 1024,
  config: {
    endpoint: 's3.example.com',
    prefix: 'hfl/repository',
    region: 'us-east-1',
    s3_url_style: 'path',
    use_tls: true,
    quota_gb: 100,
    quota_alert_enabled: true,
    quota_alert_threshold: 80,
    access_key_id: 'saved-access-key',
    secret_access_key: 'saved-secret',
  },
}

async function mountForm() {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  const wrapper = mount(EditS3Repo, {
    global: {
      plugins: [ElementPlus, i18n],
      stubs: {
        teleport: true,
        ElSelect: true,
        ElOption: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

function saveButton(wrapper: ReturnType<typeof mount>) {
  const button = wrapper.findAll('button').find(candidate => candidate.text().includes('Save Changes'))
  if (!button) throw new Error('Save Changes button was not rendered')
  return button
}

describe('EditS3Repo save behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.getStorageRepository.mockResolvedValue(repository)
    mocks.updateStorageRepository.mockResolvedValue(repository)
  })

  it('submits only quota configuration and skips authentication verification for a quota-only edit', async () => {
    const wrapper = await mountForm()
    wrapper.findAllComponents(ElInputNumber)[0].vm.$emit('update:modelValue', 200)

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.verifyStorageRepositoryAccess).not.toHaveBeenCalled()
    expect(mocks.updateStorageRepository).toHaveBeenCalledWith(42, {
      name: 'Object storage',
      config: {
        quota_gb: 200,
        quota_unit: 'GB',
        quota_alert_enabled: true,
        quota_alert_threshold: 80,
      },
    })
    wrapper.unmount()
  })

  it('shows a normal save error instead of an authentication dialog when PATCH fails', async () => {
    mocks.updateStorageRepository.mockRejectedValue(new Error('Repository update rejected'))
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const wrapper = await mountForm()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(errorMessage).toHaveBeenCalledWith({
      message: 'Repository update rejected',
      grouping: true,
    })
    expect(wrapper.find('.edit-s3-verify-dialog').exists()).toBe(false)
    wrapper.unmount()
  })
})
