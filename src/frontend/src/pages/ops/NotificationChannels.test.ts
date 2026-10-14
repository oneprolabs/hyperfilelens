// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import NotificationChannels from './NotificationChannels.vue'

const mocks = vi.hoisted(() => ({ api: vi.fn(), push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push }),
  onBeforeRouteLeave: () => {},
}))
vi.mock('../../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref({ id: 1 }), getEffectiveOrgKey: () => 'test-org' }
})
vi.mock('../../composables/useOpsMenus', () => ({ useOpsMenus: () => [] }))
vi.mock('../../lib/api', () => ({
  api: mocks.api,
  apiErrorMessageI18n: (error: unknown) => error instanceof Error ? error.message : 'Request failed',
  isAbortError: (error: unknown) => (error as { name?: string })?.name === 'AbortError',
}))

const channel = { id: 7, name: 'Alert Email', type: 'email', enabled: true, config: { to_emails: 'test@example.test' } }
const wrappers: ReturnType<typeof mount>[] = []
const global = {
  plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })],
  directives: { 'table-column-resize': {} },
  stubs: {
    ModulePage: { props: ['title'], template: '<main><h1>{{ title }}</h1><slot /></main>' },
    HflTablePanel: {
      template: '<section><slot name="toolbar" /><slot name="toolbar-actions" /><slot name="toolbar-utility" /><slot /><slot name="table" :table-max-height="400" /><slot name="footer" /></section>',
    },
    HflPagination: true,
    DangerConfirmDialog: true,
    ElDrawer: {
      props: ['modelValue'],
      template: '<aside v-if="modelValue"><header><slot name="header" /></header><slot /></aside>',
    },
  },
}
function render() {
  const wrapper = mount(NotificationChannels, { global })
  wrappers.push(wrapper)
  return wrapper
}
async function settleCapabilities() {
  await flushPromises()
  await vi.waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/api/v1/iam/resource-capabilities/', expect.any(Object)))
  await flushPromises()
}

describe('Notification Channels capability initialization', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('matchMedia', (media: string) => ({
      media, matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }))
    mocks.api.mockImplementation(async (url: string, options?: { body?: string }) => {
      if (url === '/api/v1/iam/resource-capabilities/') {
        const refs = JSON.parse(options?.body || '{}').resources
        return {
          code: 0, data: {
            organization_key: 'test-org',
            resources: refs.map((row: object) => ({ ...row, view: true, manage: true, delete: true, test: true })),
            create: { notification_channel: true },
          },
        }
      }
      if (url.includes('/stats/')) return { total: 1, enabled: 1, disabled: 0, enabled_rate: 100 }
      if (url.includes('/details/')) return { channel, associated_policies: [], notification_logs: [], stats: {} }
      if (url.startsWith('/api/v1/notifications/channels/')) return { count: 1, results: [channel] }
      throw new Error(`Unexpected API request: ${url}`)
    })
  })
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('mounts with the real immediate capability watcher and loads the channel list', async () => {
    // Do not stub useResourceCapabilities: its immediate watcher must read the
    // page's refs during setup, reproducing any initialization-order regression.
    const wrapper = render()
    await settleCapabilities()
    expect(wrapper.find('h1').text()).toBe('Notification Channels')
    expect(wrapper.find('.hfl-table-name-link').text()).toBe(channel.name)
    const requests = mocks.api.mock.calls.filter(([url]) => url === '/api/v1/iam/resource-capabilities/')
    expect(requests.some(([, options]) => JSON.parse(options.body).resources
      .some((row: { resource_id: number }) => row.resource_id === channel.id))).toBe(true)
    expect(wrapper.findAll('button').find((button) => button.text() === 'Add')!.attributes('disabled')).toBeUndefined()
  })

  it('loads channel details without losing the resource capability reference', async () => {
    const wrapper = render()
    await settleCapabilities()
    await wrapper.find('.hfl-table-name-link').trigger('click')
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/notifications/channels/7/details/')
    expect(wrapper.find('aside header').text()).toBe(channel.name)
    expect(wrapper.find('aside').text()).not.toContain('Request failed')
  })

  it('shows a list error instead of failing setup when the channel API fails', async () => {
    const original = mocks.api.getMockImplementation()!
    mocks.api.mockImplementation((url: string, options?: { body?: string }) => url.includes('/channels/?')
      ? Promise.reject(new Error('Channels are temporarily unavailable'))
      : original(url, options))
    const wrapper = render()
    await settleCapabilities()
    expect(wrapper.text()).toContain('Channels are temporarily unavailable')
    expect(wrapper.find('h1').text()).toBe('Notification Channels')
  })
})
