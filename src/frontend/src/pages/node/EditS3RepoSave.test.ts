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
  getTask: vi.fn(),
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

vi.mock('../../lib/taskApi', () => ({
  getTask: mocks.getTask,
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
    vi.useRealTimers()
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

  it('accepts a completed credential verification task before leaving the page', async () => {
    mocks.updateStorageRepository.mockResolvedValue({
      repository,
      task: { task_uuid: 'credential-task', status: 'success' },
    })
    const successMessage = vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const wrapper = await mountForm()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.getTask).not.toHaveBeenCalled()
    expect(successMessage).toHaveBeenCalled()
    expect(mocks.routerPush).toHaveBeenCalledWith({ path: '/node/repositories', query: { tab: 's3' } })
    wrapper.unmount()
  })

  it('shows the worker verification error and stays on the edit page', async () => {
    mocks.updateStorageRepository.mockResolvedValue({
      repository,
      task: {
        task_uuid: 'credential-task',
        status: 'failed',
        error_message: 'The new credentials cannot open this repository.',
      },
    })
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const wrapper = await mountForm()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(errorMessage).toHaveBeenCalledWith({
      message: 'The new credentials cannot open this repository.',
      grouping: true,
    })
    expect(mocks.routerPush).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('reports a still-running verification task without claiming the save failed', async () => {
    mocks.updateStorageRepository.mockResolvedValue({
      repository,
      task: { task_uuid: 'credential-task', status: 'pending' },
    })
    mocks.getTask.mockResolvedValue({
      task_uuid: 'credential-task',
      status: 'running',
    })
    const warningMessage = vi.spyOn(ElMessage, 'warning').mockImplementation(() => undefined as never)
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const wrapper = await mountForm()
    vi.useFakeTimers()

    await saveButton(wrapper).trigger('click')
    await vi.advanceTimersByTimeAsync(180_000)
    await flushPromises()

    expect(mocks.getTask).toHaveBeenCalledTimes(180)
    expect(warningMessage).toHaveBeenCalledWith({
      message: en.repositoriesPage.editS3Repo.verificationContinues,
      grouping: true,
    })
    expect(errorMessage).not.toHaveBeenCalled()
    expect(mocks.routerPush).toHaveBeenCalledWith({ path: '/node/repositories', query: { tab: 's3' } })
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('keeps a verification task pending when task polling is interrupted', async () => {
    mocks.updateStorageRepository.mockResolvedValue({
      repository,
      task: { task_uuid: 'credential-task', status: 'pending' },
    })
    mocks.getTask.mockRejectedValue(new Error('Temporary network failure'))
    const warningMessage = vi.spyOn(ElMessage, 'warning').mockImplementation(() => undefined as never)
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const wrapper = await mountForm()
    vi.useFakeTimers()

    await saveButton(wrapper).trigger('click')
    await vi.advanceTimersByTimeAsync(1_000)
    await flushPromises()

    expect(warningMessage).toHaveBeenCalledWith({
      message: en.repositoriesPage.editS3Repo.verificationContinues,
      grouping: true,
    })
    expect(errorMessage).not.toHaveBeenCalled()
    expect(mocks.routerPush).toHaveBeenCalledWith({ path: '/node/repositories', query: { tab: 's3' } })
    wrapper.unmount()
    vi.useRealTimers()
  })
})
