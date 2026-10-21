// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import { useAppPrimaryNav } from './useAppPrimaryNav'
import { useInsightSideNav } from './useInsightSideNav'

const organization = ref<string[]>([])
const group = ref<string[]>([])
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/insight/copilot' }) }))
vi.mock('./useTenantPermissions', () => ({
  useTenantPermissions: () => ({
    has: (key: string, organizationOnly = false) =>
      organization.value.includes(key) || (!organizationOnly && group.value.includes(key)),
  }),
}))

function navigation() {
  let primary!: ReturnType<typeof useAppPrimaryNav>
  let sidebar!: ReturnType<typeof useInsightSideNav>
  const wrapper = mount(defineComponent({
    setup() {
      primary = useAppPrimaryNav()
      sidebar = useInsightSideNav()
      return () => h('div')
    },
  }), { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
  return { primary, sidebar, wrapper }
}

describe('Insights navigation visibility', () => {
  beforeEach(() => { organization.value = []; group.value = [] })

  it('hides inaccessible Insights and Protection for a Configuration-only user, on both primary navigation surfaces', () => {
    organization.value = ['configuration.roles.manage']
    const { primary, sidebar, wrapper } = navigation()
    expect(primary.items.value.map(item => item.to)).toEqual(['/', '/node'])
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/insight')).toBe(false)
    expect(sidebar.value).toEqual([])
    wrapper.unmount()
  })

  it.each([
    ['insights.chats.view', '/insight/copilot'],
    ['insights.usage.view', '/insight/usage'],
    ['insights.private_data_gateways.view', '/insight/gateways'],
  ])('keeps Insights but only the authorized page for %s', (permission, path) => {
    organization.value = [permission]
    const { primary, sidebar, wrapper } = navigation()
    expect(primary.items.value.some(item => item.to === '/insight')).toBe(true)
    expect(sidebar.value.flatMap(section => section.children?.map(item => item.to) || [])).toEqual([path])
    wrapper.unmount()
  })

  it('reacts to grants and revocation, including Group-only access', () => {
    const { primary, sidebar, wrapper } = navigation()
    expect(primary.items.value.some(item => item.to === '/insight')).toBe(false)
    group.value = ['insights.private_data_gateways.view']
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/insight')).toBe(true)
    expect(sidebar.value.flatMap(section => section.children?.map(item => item.to) || [])).toEqual(['/insight/gateways'])
    group.value = []
    expect(primary.items.value.some(item => item.to === '/insight')).toBe(false)
    wrapper.unmount()
  })
})
