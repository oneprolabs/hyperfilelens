// @vitest-environment jsdom

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus, {
  ElButton,
  ElFormItem,
  ElInput,
  ElMessage,
  ElOption,
  ElRadioGroup,
  ElSelect,
} from 'element-plus'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import AddNasRepository from './AddNasRepository.vue'

const mocks = vi.hoisted(() => ({
  createStorageRepository: vi.fn(),
  preflightNasRepositoryCreate: vi.fn(),
  showNasDraftPreflightGuidance: vi.fn(),
  listAllNodes: vi.fn(),
  routerPush: vi.fn(),
}))

vi.mock('../../lib/storageRepositoryApi', () => ({
  createStorageRepository: mocks.createStorageRepository,
  storageRepositoryCreateErrorMessage: () => 'Unable to create repository',
}))

vi.mock('../../lib/nodeApi', () => ({
  listAllNodes: mocks.listAllNodes,
}))

vi.mock('../../lib/nasDraftPreflight', () => ({
  preflightNasRepositoryCreate: mocks.preflightNasRepositoryCreate,
  showNasDraftPreflightGuidance: mocks.showNasDraftPreflightGuidance,
}))

vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRouter: () => ({
    push: mocks.routerPush,
    resolve: () => ({ href: '/node/proxy-agents' }),
  }),
}))

const proxyNodes = [
  { id: 17, name: 'proxy-alpha', role: 'proxy', ip_address: '192.168.10.17' },
  { id: 23, name: 'proxy-beta', role: 'proxy', ip_address: '192.168.10.23' },
  { id: 99, name: 'source-agent', role: 'agent', ip_address: '192.168.10.99' },
]

async function mountForm(props: { embedded?: boolean } = {}) {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  const wrapper = mount(AddNasRepository, {
    props,
    global: { plugins: [ElementPlus, i18n] },
  })
  await flushPromises()
  return wrapper
}

function proxySelect(wrapper: VueWrapper) {
  const select = wrapper
    .findAllComponents(ElSelect)
    .find((component) => component.classes().includes('add-nas-select-row__select'))
  if (!select) throw new Error('Proxy Host select was not rendered')
  return select
}

function fieldInput(wrapper: VueWrapper, label: string) {
  const formItem = wrapper
    .findAllComponents(ElFormItem)
    .find((component) => component.props('label') === label)
  if (!formItem) throw new Error(`Form field was not rendered: ${label}`)
  const input = formItem.findComponent(ElInput)
  if (!input.exists()) throw new Error(`Input was not rendered: ${label}`)
  return input
}

async function fillRequiredNfsFields(wrapper: VueWrapper) {
  wrapper.findComponent(ElRadioGroup).vm.$emit('update:modelValue', 'nfs')
  await nextTick()
  fieldInput(wrapper, en.addNasRepo.fieldNfsHost).vm.$emit('update:modelValue', '192.168.50.10')
  fieldInput(wrapper, en.addNasRepo.fieldNfsExport).vm.$emit('update:modelValue', '/exports/backup')
  fieldInput(wrapper, en.repositoriesPage.fieldRepoName).vm.$emit('update:modelValue', 'Primary NAS')
  await nextTick()
}

async function fillRequiredSmbFields(wrapper: VueWrapper) {
  fieldInput(wrapper, en.addNasRepo.fieldSmbHost).vm.$emit('update:modelValue', '192.168.8.82')
  fieldInput(wrapper, en.addNasRepo.fieldSmbShare).vm.$emit('update:modelValue', 'smb-share')
  fieldInput(wrapper, en.repositoriesPage.fieldSmbUsername).vm.$emit('update:modelValue', 'backup')
  fieldInput(wrapper, en.repositoriesPage.fieldSmbPassword).vm.$emit('update:modelValue', 'secret')
  fieldInput(wrapper, en.repositoriesPage.fieldRepoName).vm.$emit('update:modelValue', 'Primary SMB')
  await nextTick()
}

function previewValue(wrapper: VueWrapper, label: string) {
  const row = wrapper
    .findAll('.add-form-preview-row')
    .find((candidate) => candidate.find('.add-form-preview-row__label').text() === label)
  if (!row) throw new Error(`Preview row was not rendered: ${label}`)
  return row.find('.add-form-preview-row__value').text()
}

function submitButton(wrapper: VueWrapper) {
  const button = wrapper
    .findAllComponents(ElButton)
    .find((component) => component.props('type') === 'primary')
  if (!button) throw new Error('Primary submit button was not rendered')
  return button
}

describe('AddNasRepository Proxy decision', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listAllNodes.mockResolvedValue(proxyNodes)
    mocks.createStorageRepository.mockResolvedValue({ id: 41, name: 'Primary NAS' })
    mocks.preflightNasRepositoryCreate.mockResolvedValue(undefined)
    mocks.showNasDraftPreflightGuidance.mockResolvedValue(false)
  })

  it('starts undecided with a required Proxy-oriented preview and direct access last', async () => {
    const wrapper = await mountForm()
    const select = proxySelect(wrapper)
    const formItem = wrapper
      .findAllComponents(ElFormItem)
      .find((component) => component.classes().includes('add-nas-bind-form-item'))

    expect(formItem?.props('required')).toBe(true)
    expect(select.props('modelValue')).toBeUndefined()
    expect(select.props('clearable')).toBe(true)
    expect(wrapper.find('.nas-proxy-topology--direct').exists()).toBe(false)
    expect(previewValue(wrapper, en.addNasRepo.fieldSourceProxyNode)).toBe('—')
    expect(previewValue(wrapper, en.repositoriesPage.fieldAccessPath)).toBe(en.addNasRepo.accessPathWithProxy)
    expect(submitButton(wrapper).text()).toContain('Submit and initialize')

    const optionLabels = wrapper.findAllComponents(ElOption).map(option => option.props('label'))
    expect(optionLabels).toEqual([
      'proxy-alpha (192.168.10.17)',
      'proxy-beta (192.168.10.23)',
      en.addNasRepo.optionNoProxy,
    ])
    expect(wrapper.text()).toContain(en.addNasRepo.hintProxyUndecided)
  })

  it('blocks submission until the user makes an explicit Proxy decision', async () => {
    const warning = vi.spyOn(ElMessage, 'warning')
    const wrapper = await mountForm({ embedded: true })
    await fillRequiredNfsFields(wrapper)

    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.createStorageRepository).not.toHaveBeenCalled()
    expect(warning).toHaveBeenCalledWith({
      message: en.addNasRepo.errProxyDecisionRequired,
      grouping: true,
    })
    warning.mockRestore()
  })

  it('submits a selected Proxy and returns to the undecided state when cleared', async () => {
    const wrapper = await mountForm()
    await fillRequiredNfsFields(wrapper)
    const select = proxySelect(wrapper)

    select.vm.$emit('update:modelValue', 17)
    await nextTick()
    expect(wrapper.find('.nas-proxy-topology--direct').exists()).toBe(false)
    expect(previewValue(wrapper, en.addNasRepo.fieldSourceProxyNode)).toBe('proxy-alpha')
    expect(wrapper.findAllComponents(ElFormItem).some(
      component => component.props('label') === en.repositoriesPage.fieldRepositoryServerHost,
    )).toBe(false)

    await submitButton(wrapper).trigger('click')
    await flushPromises()
    expect(mocks.createStorageRepository).toHaveBeenCalledWith(expect.objectContaining({
      bind_node_type: 'proxy',
      bind_node_id: 17,
    }))
    expect(mocks.createStorageRepository.mock.calls[0][0].config)
      .not.toHaveProperty('proxy_repository_server_host')

    select.vm.$emit('clear')
    await nextTick()
    expect(select.props('modelValue')).toBeUndefined()
    expect(wrapper.find('.nas-proxy-topology--direct').exists()).toBe(false)
    expect(previewValue(wrapper, en.addNasRepo.fieldSourceProxyNode)).toBe('—')
    expect(wrapper.text()).toContain(en.addNasRepo.hintProxyUndecided)
  })

  it('warns about explicit direct access and omits the frontend sentinel from the request', async () => {
    const wrapper = await mountForm()
    await fillRequiredNfsFields(wrapper)
    const select = proxySelect(wrapper)

    expect(wrapper.find('.add-nas-proxy-alert').text())
      .toContain(en.addNasRepo.bindProxyLeadItemDirectLinuxOnly)

    select.vm.$emit('update:modelValue', 0)
    await nextTick()
    expect(wrapper.find('.nas-proxy-topology--direct').exists()).toBe(true)
    const warning = wrapper.find('.add-nas-direct-warning')
    expect(warning.findAll('li').map(item => item.text())).toEqual([
      `1${en.addNasRepo.directAccessRiskLinuxOnly}`,
      `2${en.addNasRepo.directAccessRiskDependencies}`,
      `3${en.addNasRepo.directAccessRiskDifferences}`,
      `4${en.addNasRepo.directAccessRiskBindProxy}`,
    ])
    expect(previewValue(wrapper, en.addNasRepo.fieldSourceProxyNode)).toBe(en.addNasRepo.notBoundProxy)
    expect(previewValue(wrapper, en.repositoriesPage.fieldAccessPath)).toBe(en.addNasRepo.accessPathDirect)
    expect(submitButton(wrapper).text()).toContain('Save configuration')
    expect(wrapper.findAllComponents(ElFormItem).some(
      component => component.props('label') === en.repositoriesPage.fieldRepositoryServerHost,
    )).toBe(false)

    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.createStorageRepository).toHaveBeenCalledOnce()
    const serializedPayload = JSON.parse(JSON.stringify(mocks.createStorageRepository.mock.calls[0][0]))
    expect(serializedPayload).not.toHaveProperty('bind_node_type')
    expect(serializedPayload).not.toHaveProperty('bind_node_id')
    expect(serializedPayload.config).not.toHaveProperty('proxy_repository_server_host')
  })

  it('blocks Repository creation when the Proxy SMB preflight fails', async () => {
    const preflightError = new Error('SMB UTF-8 support is unavailable')
    mocks.preflightNasRepositoryCreate.mockRejectedValue(preflightError)
    mocks.showNasDraftPreflightGuidance.mockResolvedValue(true)
    const wrapper = await mountForm({ embedded: true })
    await fillRequiredSmbFields(wrapper)
    proxySelect(wrapper).vm.$emit('update:modelValue', 17)
    await nextTick()

    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.preflightNasRepositoryCreate).toHaveBeenCalledOnce()
    expect(mocks.createStorageRepository).not.toHaveBeenCalled()
    expect(mocks.showNasDraftPreflightGuidance).toHaveBeenCalledWith(
      preflightError,
      expect.any(Function),
      'proxy-alpha',
    )
  })
})
