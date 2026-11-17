// @vitest-environment jsdom
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'

const mocks = vi.hoisted(() => ({
  api: vi.fn(), complete: vi.fn(), confirm: vi.fn(), notify: vi.fn(),
  currentUser: { value: { id: 7, access_profile: { org_key: 'org-a', org_name: 'Organization A' } } },
  getEffectiveOrgKey: vi.fn(() => 'org-a'),
}))
vi.mock('../lib/api', () => ({ api: mocks.api }))
vi.mock('../lib/organizationSwitch', () => ({ completeOrganizationSwitch: mocks.complete }))
vi.mock('../lib/notify', () => ({ notifyError: mocks.notify }))
vi.mock('element-plus', async (load) => ({ ...(await load<typeof import('element-plus')>()), ElMessageBox: { confirm: mocks.confirm } }))
vi.mock('./useAuth', () => ({ currentUser: mocks.currentUser, getEffectiveOrgKey: mocks.getEffectiveOrgKey }))

const rows = [{ id: 1, key: 'org-a', name: 'Organization A' }, { id: 2, key: 'org-b', name: 'Personal' }]
const response = { access_profile: { org_key: 'org-b', landing_path: '/protection/backups' } }
async function render() {
  const { useOrganizationSwitcher } = await import('./useOrganizationSwitcher')
  let state!: ReturnType<typeof useOrganizationSwitcher>
  const wrapper = mount(defineComponent({ setup() { state = useOrganizationSwitcher(); return state }, template: '<span>{{ currentName }}</span>' }))
  await flushPromises()
  return { wrapper, state }
}

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  mocks.currentUser.value = { id: 7, access_profile: { org_key: 'org-a', org_name: 'Organization A' } }
  mocks.getEffectiveOrgKey.mockReturnValue('org-a')
  mocks.api.mockResolvedValue(rows)
  mocks.confirm.mockResolvedValue('confirm')
})

describe('Enterprise organization switching', () => {
  it('shares one directory request across desktop and mobile, without creation policy requests', async () => {
    let finish!: (value: unknown) => void
    mocks.api.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const first = await render()
    const second = await render()
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/iam/my-organizations/')
    finish(rows)
    await flushPromises()
    expect(first.state.showSwitcher.value).toBe(true)
    expect(second.state.currentName.value).toBe('Organization A')
    first.wrapper.unmount(); second.wrapper.unmount()
  })

  it('keeps the single organization available as state without enabling switching', async () => {
    mocks.api.mockResolvedValue([rows[0]])
    const { wrapper, state } = await render()
    expect(state.showSwitcher.value).toBe(false)
    expect(state.currentName.value).toBe('Organization A')
    wrapper.unmount()
  })

  it('validates the target before committing context and navigates to its landing page', async () => {
    const { wrapper, state } = await render()
    mocks.api.mockResolvedValue(response)
    expect(await state.switchOrganization('org-b')).toBe(true)
    expect(mocks.api).toHaveBeenLastCalledWith('/api/v1/iam/switch-organization/', { method: 'POST', body: JSON.stringify({ org_key: 'org-b' }) })
    expect(mocks.complete).toHaveBeenCalledWith('org-b', '/protection/backups')
    expect(mocks.currentUser.value.access_profile.org_key).toBe('org-a')
    wrapper.unmount()
  })

  it.each([
    { access_profile: { org_key: 'org-a', landing_path: '/' } },
    { access_profile: { org_key: 'org-b', landing_path: '//foreign.example' } },
    { access_profile: { org_key: 'org-b', landing_path: '/platform-ops/users' } },
    {},
  ])('rejects mismatched or unsafe responses without changing organization: %j', async (data) => {
    const { wrapper, state } = await render()
    mocks.api.mockResolvedValue(data)
    await expect(state.switchOrganization('org-b')).rejects.toThrow()
    expect(mocks.complete).not.toHaveBeenCalled()
    expect(state.switching.value).toBe(false)
    wrapper.unmount()
  })

  it('keeps the old organization after a failed or revoked request', async () => {
    const { wrapper, state } = await render()
    mocks.api.mockRejectedValue(new Error('Denied'))
    await expect(state.switchOrganization('org-b')).rejects.toThrow('Denied')
    expect(mocks.complete).not.toHaveBeenCalled()
    expect(state.currentKey.value).toBe('org-a')
    wrapper.unmount()
  })

  it('blocks duplicate switches and ignores results after an account change', async () => {
    const { wrapper, state } = await render()
    let finish!: (value: unknown) => void
    mocks.api.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const pending = state.switchOrganization('org-b')
    expect(await state.switchOrganization('org-b')).toBe(false)
    mocks.currentUser.value = { id: 8, access_profile: { org_key: 'org-a', org_name: 'Other account' } }
    finish(response)
    expect(await pending).toBe(false)
    expect(mocks.complete).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('ignores a pending response if the current organization has changed', async () => {
    const { wrapper, state } = await render()
    let finish!: (value: unknown) => void
    mocks.api.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const pending = state.switchOrganization('org-b')
    mocks.getEffectiveOrgKey.mockReturnValue('org-c')
    finish(response)
    expect(await pending).toBe(false)
    expect(mocks.complete).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('retries a failed directory load without losing the current organization name', async () => {
    mocks.api.mockRejectedValueOnce(new Error('Network'))
    const { wrapper, state } = await render()
    expect(state.loadFailed.value).toBe(true)
    expect(state.currentName.value).toBe('Organization A')
    await state.loadOrganizations(true)
    expect(state.loadFailed.value).toBe(false)
    expect(state.organizations.value).toHaveLength(2)
    wrapper.unmount()
  })

  it('does not request a switch to the current organization', async () => {
    const { wrapper, state } = await render()
    expect(await state.switchOrganization('org-a')).toBe(false)
    expect(mocks.api).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('renders names, confirms before leaving, and never offers organization creation', async () => {
    const { default: OrgSwitcher } = await import('../components/OrgSwitcher.vue')
    const { ElDropdown, ElDropdownItem, ElDropdownMenu } = await import('element-plus')
    const wrapper = mount(OrgSwitcher, { global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      components: { ElDropdown, ElDropdownItem, ElDropdownMenu },
    } })
    await flushPromises()
    expect(wrapper.text()).toContain('Organization A')
    expect(wrapper.html()).not.toContain('__create__')
    mocks.confirm.mockRejectedValueOnce('cancel')
    wrapper.findComponent(ElDropdown).vm.$emit('command', 'org-b')
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(1)
    mocks.api.mockResolvedValue(response)
    wrapper.findComponent(ElDropdown).vm.$emit('command', 'org-b')
    await flushPromises()
    expect(mocks.confirm).toHaveBeenCalledWith(
      en.nav.switchOrganizationNotice.replace('{organization}', 'Personal'),
      en.nav.switchOrganization,
      expect.objectContaining({ confirmButtonText: en.nav.switchOrganizationConfirm }),
    )
    expect(mocks.complete).toHaveBeenCalledWith('org-b', '/protection/backups')
    wrapper.unmount()
  })

  it.each(['desktop', 'mobile'] as const)('hides the complete %s switcher for one organization', async (variant) => {
    mocks.api.mockResolvedValue([rows[0]])
    const { default: OrgSwitcher } = await import('../components/OrgSwitcher.vue')
    const { ElDropdown, ElDropdownItem, ElDropdownMenu } = await import('element-plus')
    const wrapper = mount(OrgSwitcher, { props: { variant }, global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      components: { ElDropdown, ElDropdownItem, ElDropdownMenu },
    } })
    await flushPromises()
    expect(wrapper.find('.org-switcher').exists()).toBe(false)
    expect(wrapper.text()).toBe('')
    wrapper.unmount()
  })

  it('keeps the entry hidden during loading and on failure, and shows it after a successful reload', async () => {
    let reject!: (error: Error) => void
    mocks.api.mockReturnValueOnce(new Promise((_, failure) => { reject = failure }))
    const { wrapper: stateWrapper, state } = await render()
    const { default: OrgSwitcher } = await import('../components/OrgSwitcher.vue')
    const { ElDropdown, ElDropdownItem, ElDropdownMenu } = await import('element-plus')
    const wrapper = mount(OrgSwitcher, { global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      components: { ElDropdown, ElDropdownItem, ElDropdownMenu },
    } })
    expect(wrapper.find('.org-switcher').exists()).toBe(false)
    reject(new Error('Network'))
    await flushPromises()
    expect(state.loadFailed.value).toBe(true)
    expect(wrapper.find('.org-switcher').exists()).toBe(false)
    mocks.api.mockResolvedValue(rows)
    await state.loadOrganizations(true)
    await flushPromises()
    expect(wrapper.find('.org-switcher__trigger').exists()).toBe(true)
    expect(wrapper.text()).toContain('Organization A')
    stateWrapper.unmount()
    wrapper.unmount()
  })
})
