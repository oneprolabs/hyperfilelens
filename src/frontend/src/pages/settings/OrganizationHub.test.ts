// @vitest-environment jsdom
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OrganizationHub from './OrganizationHub.vue'
import { en } from '../../locales/en'
import { currentUser } from '../../composables/useAuth'

const mocks = vi.hoisted(() => ({ api: vi.fn(), orgKey: 'current' }))
vi.mock('../../lib/api', async () => ({
  api: mocks.api,
  isAbortError: (await import('../../lib/errors')).isAbortError,
}))
vi.mock('../../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { getEffectiveOrgKey: () => mocks.orgKey, currentUser: ref(null) }
})
vi.mock('../../composables/useNodeSideNav', () => ({ useNodeSideNav: () => [] }))
const global = {
  plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })],
  stubs: { ModulePage: { template: '<main><slot /></main>' } },
}
enableAutoUnmount(afterEach)

describe('Current organization overview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.orgKey = 'current'
    currentUser.value = {
      id: 1, email: 'member@test.local', username: 'member',
      access_profile: {
        org_key: 'current', role: 'custom-reader',
        permissions: ['configuration.organizations.view'], group_permissions: [],
        visible_features: [], available_platforms: [], landing_path: '/',
      },
    }
    mocks.api.mockResolvedValue([{
      id: 1, key: 'current', name: 'Current Team', owner_email: 'owner@test.local', member_count: 3,
    }])
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows current organization identity and member count', async () => {
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).toContain('Current Team')
    expect(wrapper.text()).toContain('owner@test.local')
    expect(wrapper.text()).toContain('Organization ID')
    expect(wrapper.text()).toContain('Members')
    expect(wrapper.text()).toContain('3')
    wrapper.unmount()
  })

  it('keeps the original four details without extra overview content or actions', async () => {
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.findAll('dt').map(label => label.text())).toEqual([
      'Organization ID', 'Members', 'Owner', 'Created',
    ])
    expect(wrapper.find('h2').text()).toBe('Current Team')
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Organization overview')
    expect(wrapper.text()).not.toContain('Identity and ownership')
    expect(wrapper.text()).not.toContain('Status')
    expect(wrapper.find('[title]').attributes('title')).toBe(en.settings.org.keyHint)
    wrapper.unmount()
  })

  it('never substitutes another organization when the active org is absent', async () => {
    mocks.api.mockResolvedValue([{ id: 2, key: 'other', name: 'Other Team' }])
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Other Team')
    expect(wrapper.text()).toContain('No organizations yet')
    wrapper.unmount()
  })

  it('does not substitute the first organization when there is no active scope', async () => {
    mocks.orgKey = ''
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(mocks.api).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('Current Team')
    expect(wrapper.text()).toContain('No organizations yet')
    wrapper.unmount()
  })

  it('shows access denied on a backend 403 rather than a load failure or retry', async () => {
    mocks.api.mockRejectedValue({ status: 403 })
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).toContain(en.settings.org.accessDenied)
    expect(wrapper.text()).not.toContain(en.settings.org.loadFailed)
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not report a load failure for route cancellation', async () => {
    mocks.api.mockRejectedValue(new DOMException('Cancelled', 'AbortError'))
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).not.toContain(en.settings.org.loadFailed)
    expect(wrapper.text()).not.toContain(en.settings.org.accessDenied)
    wrapper.unmount()
  })

  it.runIf(__HFL_EXTENSIONS_FRONTEND__)('requires an organization grant, not a Role name or Group grant', async () => {
    currentUser.value!.access_profile!.role = 'owner'
    currentUser.value!.access_profile!.permissions = []
    currentUser.value!.access_profile!.group_permissions = ['configuration.organizations.view']
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(mocks.api).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain(en.settings.org.accessDenied)
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it.runIf(!__HFL_EXTENSIONS_FRONTEND__)('preserves Community permission bypass', async () => {
    currentUser.value!.access_profile!.permissions = []
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).toContain('Current Team')
    wrapper.unmount()
  })

  it.runIf(__HFL_EXTENSIONS_FRONTEND__)('clears displayed data on revocation without making another request', async () => {
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    currentUser.value!.access_profile!.permissions = []
    await flushPromises()
    expect(wrapper.text()).not.toContain('Current Team')
    expect(wrapper.text()).toContain(en.settings.org.accessDenied)
    expect(mocks.api).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it.runIf(__HFL_EXTENSIONS_FRONTEND__)('ignores a late response after permission revocation', async () => {
    let resolve!: (value: unknown) => void
    mocks.api.mockReturnValue(new Promise(done => { resolve = done }))
    const wrapper = mount(OrganizationHub, { global })
    currentUser.value!.access_profile!.permissions = []
    resolve([{ id: 1, key: 'current', name: 'Stale Team' }])
    await flushPromises()
    expect(wrapper.text()).not.toContain('Stale Team')
    expect(wrapper.text()).toContain(en.settings.org.accessDenied)
    wrapper.unmount()
  })

  it('ignores late responses after an organization switch, including switch-back', async () => {
    const responses: ((value: unknown) => void)[] = []
    mocks.api.mockImplementation(() => new Promise(done => { responses.push(done) }))
    const wrapper = mount(OrganizationHub, { global })
    mocks.orgKey = 'other'
    currentUser.value!.access_profile!.org_key = 'other'
    await flushPromises()
    mocks.orgKey = 'current'
    currentUser.value!.access_profile!.org_key = 'current'
    await flushPromises()
    expect(responses).toHaveLength(3)
    responses[2]!([{ id: 1, key: 'current', name: 'Latest Team' }])
    await flushPromises()
    responses[0]!([{ id: 1, key: 'current', name: 'Stale Team' }])
    responses[1]!([{ id: 2, key: 'other', name: 'Other Team' }])
    await flushPromises()
    expect(wrapper.text()).toContain('Latest Team')
    expect(wrapper.text()).not.toContain('Stale Team')
    expect(wrapper.text()).not.toContain('Other Team')
    wrapper.unmount()
  })

  it('clears the old card and uses the updated stored scope before loading the next organization', async () => {
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).toContain('Current Team')
    let reject!: (error: unknown) => void
    mocks.api.mockReturnValue(new Promise((_resolve, fail) => { reject = fail }))
    // Match setUser's order: reactive profile first, localStorage second.
    currentUser.value!.access_profile!.org_key = 'other'
    mocks.orgKey = 'other'
    await flushPromises()
    expect(wrapper.text()).not.toContain('Current Team')
    reject({ status: 403 })
    await flushPromises()
    expect(wrapper.text()).toContain(en.settings.org.accessDenied)
    expect(wrapper.text()).not.toContain(en.settings.org.loadFailed)
    wrapper.unmount()
  })

  it('distinguishes load failure from an empty org and provides retry', async () => {
    mocks.api.mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce([
      { id: 1, key: 'current', name: 'Recovered Team' },
    ])
    const wrapper = mount(OrganizationHub, { global })
    await flushPromises()
    expect(wrapper.text()).toContain('Unable to load the organization.')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Recovered Team')
    wrapper.unmount()
  })
})
