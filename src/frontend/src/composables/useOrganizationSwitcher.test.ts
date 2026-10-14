// @vitest-environment jsdom

import { defineComponent, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'
import OrgSwitcher from '../components/OrgSwitcher.vue'
import { useOrganizationSwitcher } from './useOrganizationSwitcher'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  currentUser: { value: { id: 7, access_profile: { org_key: 'org-a' } } },
  fetchCurrentUser: vi.fn(),
  getEffectiveOrgKey: vi.fn(() => 'org-a'),
  setStoredOrgKey: vi.fn(),
  push: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))

vi.mock('../lib/api', () => ({ api: mocks.api }))
vi.mock('./useAuth', () => ({
  currentUser: mocks.currentUser,
  fetchCurrentUser: mocks.fetchCurrentUser,
  getEffectiveOrgKey: mocks.getEffectiveOrgKey,
  setStoredOrgKey: mocks.setStoredOrgKey,
}))

describe('useOrganizationSwitcher', () => {
  it('shares one organization request across desktop and mobile consumers', async () => {
    let resolveOrganizations: (value: unknown) => void = () => undefined
    let loadOrganizations: ((force?: boolean) => Promise<void>) | undefined
    mocks.api.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOrganizations = resolve
      }),
    )
    mocks.api.mockResolvedValueOnce({ can_create: true })

    const Host = defineComponent({
      setup() {
        const { showSwitcher, loadOrganizations: load } = useOrganizationSwitcher()
        loadOrganizations = load
        return { showSwitcher }
      },
      template: '<span>{{ showSwitcher }}</span>',
    })

    const first = mount(Host)
    const second = mount(Host)
    await nextTick()

    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/iam/my-organizations/')
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/iam/organization-creation-options/')

    resolveOrganizations([
      { id: 1, key: 'org-a', name: 'Organization A' },
      { id: 2, key: 'org-b', name: 'Organization B' },
    ])
    await flushPromises()

    expect(first.text()).toBe('true')
    expect(second.text()).toBe('true')

    let resolveReload: (value: unknown) => void = () => undefined
    mocks.currentUser.value = { id: 8, access_profile: { org_key: 'org-c' } }
    mocks.api.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveReload = resolve
      }),
    )
    mocks.api.mockResolvedValueOnce({ can_create: true })

    const firstReload = loadOrganizations?.(true)
    const secondReload = loadOrganizations?.(true)
    expect(mocks.api).toHaveBeenCalledTimes(4)

    resolveReload([
      { id: 3, key: 'org-c', name: 'Organization C' },
      { id: 4, key: 'org-d', name: 'Organization D' },
    ])
    await Promise.all([firstReload, secondReload])

    const i18n = createI18n({
      legacy: false,
      locale: 'en',
      messages: { en: { nav: { orgSwitcher: 'Organization' }, organizationCreation: { title: 'Create Organization' } } },
    })
    const switchers = mount(
      defineComponent({
        components: { OrgSwitcher },
        template: '<div><OrgSwitcher /><OrgSwitcher variant="mobile" /></div>',
      }),
      { global: { plugins: [i18n] } },
    )
    await flushPromises()

    const selects = switchers.findAll('select')
    expect(selects).toHaveLength(2)
    expect(selects[0]?.attributes('id')).not.toBe(selects[1]?.attributes('id'))
    expect(switchers.findAll('label').map((label) => label.attributes('for'))).toEqual(
      selects.map((select) => select.attributes('id')),
    )
    expect(selects[0].find('option[value="__create__"]').text()).toBe('Create Organization')
    await selects[0].setValue('__create__')
    expect(mocks.push).toHaveBeenCalledWith('/account/organizations/new')

    first.unmount()
    second.unmount()
    switchers.unmount()
  })
})
