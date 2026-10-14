// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OrganizationHub from './OrganizationHub.vue'
import { en } from '../../locales/en'

const mocks = vi.hoisted(() => ({ api: vi.fn(), orgKey: 'current' }))
vi.mock('../../lib/api', () => ({ api: mocks.api }))
vi.mock('../../composables/useAuth', () => ({ getEffectiveOrgKey: () => mocks.orgKey }))
vi.mock('../../composables/useNodeSideNav', () => ({ useNodeSideNav: () => [] }))
const global = {
  plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })],
  stubs: { ModulePage: { template: '<main><slot /></main>' } },
}

describe('Current organization overview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.orgKey = 'current'
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
