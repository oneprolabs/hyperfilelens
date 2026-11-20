// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentUser } from '../../../composables/useAuth'
import { en } from '../../../locales/en'
import PersonalStorageReadiness from './PersonalStorageReadiness.vue'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../../../lib/api', () => ({ api: mocks.api, isAbortError: (error: Error) => error?.name === 'AbortError' }))
vi.mock('../../../lib/organizationCapabilities', () => ({
  organizationCapabilityAvailable: (profile: { organization_capabilities?: { managed_storage?: boolean } }) =>
    profile?.organization_capabilities?.managed_storage === true,
}))
vi.mock('../../../composables/useAuth', async () => {
  const { ref } = await import('vue')
  const currentUser = ref({ access_profile: {
    org_key: 'personal', organization_mode: 'personal', organization_capabilities: { managed_storage: true },
  } })
  return { currentUser, getEffectiveOrgKey: () => currentUser.value.access_profile.org_key }
})

const wrappers: VueWrapper[] = []
function open(props = {}) {
  const wrapper = mount(PersonalStorageReadiness, {
    props: { hasAvailableTarget: false, ...props },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElAlert: { template: '<div role="status"><slot /></div>' },
        ElButton: { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
      },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}
beforeEach(() => {
  vi.useFakeTimers()
  mocks.api.mockReset()
  currentUser.value = { access_profile: {
    org_key: 'personal', organization_mode: 'personal', organization_capabilities: { managed_storage: true },
  } } as typeof currentUser.value
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
})

describe('Personal storage readiness', () => {
  it('polls preparing storage and refreshes the selector once ready', async () => {
    mocks.api.mockResolvedValueOnce({ state: 'preparing' }).mockResolvedValueOnce({ state: 'ready' })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.text()).toContain('being prepared')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.emitted('ready')).toHaveLength(1)
    await wrapper.setProps({ hasAvailableTarget: true })
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
  })

  it('shows safe failure guidance, not management or initialization retry controls', async () => {
    mocks.api.mockResolvedValue({ state: 'failed' })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.text()).toContain('Contact your organization administrator')
    expect(wrapper.text()).not.toContain('Retry Initialization')
    expect(wrapper.find('a').exists()).toBe(false)
  })

  it('distinguishes a failed query from missing storage and lets the user retry', async () => {
    mocks.api.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({ state: 'missing' })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.text()).toContain('Unable to check')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('No backup storage is available')
  })

  it('does not call the EE endpoint for an ordinary organization', async () => {
    currentUser.value!.access_profile!.organization_mode = 'standard'
    const wrapper = open()
    await flushPromises()
    expect(mocks.api).not.toHaveBeenCalled()
    expect(wrapper.text()).toBe('')
  })

  it('does not overwrite a new organization with an old response', async () => {
    let finish!: (value: unknown) => void
    mocks.api.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      .mockResolvedValueOnce({ state: 'missing' })
    const wrapper = open()
    currentUser.value!.access_profile!.org_key = 'personal-other-context'
    await flushPromises()
    finish({ state: 'failed' })
    await flushPromises()
    expect(wrapper.text()).toContain('No backup storage is available')
    expect(wrapper.text()).not.toContain('could not be prepared')
  })

  it('stops polling when unmounted', async () => {
    mocks.api.mockResolvedValue({ state: 'preparing' })
    const wrapper = open()
    await flushPromises()
    wrapper.unmount()
    wrappers.splice(wrappers.indexOf(wrapper), 1)
    await vi.advanceTimersByTimeAsync(15000)
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })
})
