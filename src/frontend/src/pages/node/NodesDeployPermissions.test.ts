// @vitest-environment jsdom

import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { computed, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  issue: vi.fn(),
}))
const route = reactive({ query: {} as Record<string, unknown> })
const allowed = ref<string[]>([])
const loading = ref(true)
const failed = ref(false)
const organization = ref('tenant-a')
vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('../../composables/useAuth', () => ({ getEffectiveOrgKey: () => organization.value }))
vi.mock('../../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => key === 'insights.chats.view' }),
}))
vi.mock('../../composables/useResourceCapabilities', () => ({
  useResourceCapabilities: () => ({
    canCreate: (kind: string) => allowed.value.includes(kind),
    loading: computed(() => loading.value),
    failed: computed(() => failed.value),
    refresh: mocks.refresh,
  }),
}))
vi.mock('../../components/NodeLifecycleWizard.vue', async () => {
  const { defineComponent, onMounted } = await import('vue')
  return {
    default: defineComponent({
      name: 'NodeLifecycleWizard',
      props: ['role', 'allowedRoles', 'orgKey'],
      emits: ['update:role'],
      setup() { onMounted(mocks.issue) },
      template: '<div class="test-install-wizard" />',
    }),
  }
})

import NodesDeploy from './NodesDeploy.vue'

const wrappers: VueWrapper[] = []
function mountPage() {
  const wrapper = shallowMount(NodesDeploy, {
    global: {
      stubs: {
        teleport: true,
        NodeLifecycleWizard: false,
        ElButton: { template: '<button><slot /></button>' },
        ElSkeleton: true,
        ElAlert: { template: '<div class="test-error"><slot /></div>' },
      },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  route.query = { role: 'gateway' }
  allowed.value = []
  loading.value = true
  failed.value = false
  organization.value = 'tenant-a'
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('Node deployment creation capabilities', () => {
  it('waits for capabilities before mounting the token-issuing wizard', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'NodeLifecycleWizard' }).exists()).toBe(false)
    expect(mocks.issue).not.toHaveBeenCalled()
    expect(mocks.replace).not.toHaveBeenCalled()
    allowed.value = ['node:gateway']
    loading.value = false
    await flushPromises()
    expect(wrapper.findComponent({ name: 'NodeLifecycleWizard' }).exists()).toBe(true)
    expect(mocks.issue).toHaveBeenCalledOnce()
  })

  it('redirects a denied deployment without issuing enrollment', async () => {
    loading.value = false
    mountPage()
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith('/insight/copilot')
    expect(mocks.issue).not.toHaveBeenCalled()
  })

  it('offers a retry after capability failure, without issuing enrollment or redirecting', async () => {
    loading.value = false
    failed.value = true
    const wrapper = mountPage()
    await flushPromises()
    expect(mocks.issue).not.toHaveBeenCalled()
    expect(mocks.replace).not.toHaveBeenCalled()
    await wrapper.get('.test-error button').trigger('click')
    expect(mocks.refresh).toHaveBeenCalledOnce()
  })

  it('passes only authorized roles and rejects an unauthorized role change', async () => {
    route.query = {}
    allowed.value = ['node:agent']
    loading.value = false
    const wrapper = mountPage()
    await flushPromises()
    const wizard = wrapper.findComponent({ name: 'NodeLifecycleWizard' })
    expect(wizard.props('allowedRoles')).toEqual(['agent'])
    wizard.vm.$emit('update:role', 'gateway')
    await flushPromises()
    expect(wizard.props('role')).toBe('agent')
    expect(mocks.issue).toHaveBeenCalledOnce()
    expect(mocks.replace).not.toHaveBeenCalled()
  })

  it('unmounts installation while switching to an organization without creation permission', async () => {
    allowed.value = ['node:gateway']
    loading.value = false
    const wrapper = mountPage()
    await flushPromises()
    organization.value = 'tenant-b'
    allowed.value = []
    loading.value = true
    await flushPromises()
    expect(wrapper.findComponent({ name: 'NodeLifecycleWizard' }).exists()).toBe(false)
    expect(mocks.issue).toHaveBeenCalledOnce()
    expect(mocks.replace).not.toHaveBeenCalled()
    loading.value = false
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith('/insight/copilot')
  })

  it('does not reuse private creation permission to issue public enrollment', async () => {
    route.query = { role: 'gateway', gatewayScope: 'platform' }
    allowed.value = ['node:gateway']
    loading.value = false
    mountPage()
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith('/platform-ops/engine/gateways/add')
    expect(mocks.issue).not.toHaveBeenCalled()
  })
})
