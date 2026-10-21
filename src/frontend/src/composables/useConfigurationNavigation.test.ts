// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import { configurationPages } from '../lib/configurationAccess'
import { useAppPrimaryNav } from './useAppPrimaryNav'
import { useNodeSideNav } from './useNodeSideNav'

const organization = ref<string[]>([])
const group = ref<string[]>([])
const community = ref(false)
const registered = ref<string[]>([])
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/node/resources' }) }))
vi.mock('../governance/resolveRoutes', () => ({
  resolveGovernanceRoutes: () => registered.value.map(path => ({ path })),
}))
// Deliberately offer every item: the sidebar must still apply the shared policy.
vi.mock('../governance/resolveMenu', () => ({
  resolveGovernanceMenu: () => configurationPages.filter(page => page.path !== '/node/subscription')
    .map(page => ({ to: page.path, label: page.path })),
}))
vi.mock('./useTenantPermissions', () => ({
  useTenantPermissions: () => ({
    has: (key: string, organizationOnly = false) => community.value || organization.value.includes(key)
      || (!organizationOnly && group.value.includes(key)),
  }),
}))

function navigation() {
  let primary!: ReturnType<typeof useAppPrimaryNav>
  let sidebar!: ReturnType<typeof useNodeSideNav>
  const wrapper = mount(defineComponent({ setup() {
    primary = useAppPrimaryNav()
    sidebar = useNodeSideNav()
    return () => null
  } }), { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
  const paths = () => sidebar.value.flatMap(section => section.children?.map(item => item.to) || [])
  return { primary, sidebar, wrapper, paths }
}

describe('Configuration navigation consistency', () => {
  beforeEach(() => {
    organization.value = []
    group.value = []
    community.value = false
    registered.value = configurationPages.map(page => page.path.slice(1))
  })

  it('hides Configuration on both navigation surfaces, including the empty sidebar section', () => {
    organization.value = ['protection.source_hosts.view', 'insights.chats.view', 'operations.tasks.view']
    const { primary, sidebar, wrapper } = navigation()
    expect(primary.items.value.map(item => item.to)).toEqual(['/', '/protection', '/insight', '/ops'])
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/node')).toBe(false)
    expect(sidebar.value).toEqual([])
    wrapper.unmount()
  })

  it.each(configurationPages)('shows only $path for its organization grant and hides it after revocation', page => {
    const { primary, paths, sidebar, wrapper } = navigation()
    organization.value = [page.permission]
    expect(primary.items.value.some(item => item.to === '/node')).toBe(true)
    expect(paths()).toEqual([page.path])
    organization.value = []
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/node')).toBe(false)
    expect(sidebar.value).toEqual([])
    wrapper.unmount()
  })

  it.each(configurationPages)('uses only supported Group grants for $path', page => {
    group.value = [page.permission]
    const { primary, paths, wrapper } = navigation()
    expect(primary.items.value.some(item => item.to === '/node')).toBe(!page.organizationOnly)
    expect(paths()).toEqual(page.organizationOnly ? [] : [page.path])
    wrapper.unmount()
  })

  it('keeps Groups and Resources in order without advertising Organization, Members, Roles or Subscription', () => {
    group.value = configurationPages.map(page => page.permission)
    const { paths, wrapper } = navigation()
    expect(paths()).toEqual(['/node/resource-groups', '/node/resources'])
    wrapper.unmount()
  })

  it('does not advertise unregistered pages in a Community build', () => {
    community.value = true
    registered.value = ['node/subscription']
    const { primary, paths, wrapper } = navigation()
    expect(primary.items.value.some(item => item.to === '/node')).toBe(true)
    expect(paths()).toEqual(['/node/subscription'])
    registered.value = []
    expect(primary.items.value.some(item => item.to === '/node')).toBe(false)
    expect(paths()).toEqual([])
    wrapper.unmount()
  })
})
