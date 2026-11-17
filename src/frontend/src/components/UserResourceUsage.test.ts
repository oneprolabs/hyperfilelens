// @vitest-environment jsdom
import { ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import { USER_USAGE_METERS, validUserUsage, type UserUsage } from '../lib/userUsageApi'
import UserResourceUsage from './UserResourceUsage.vue'

const mocks = vi.hoisted(() => ({ api: vi.fn(), auth: { currentUser: null as unknown }, org: vi.fn() }))
vi.mock('../composables/useAuth', () => ({
  get currentUser() { return mocks.auth.currentUser },
  getEffectiveOrgKey: mocks.org,
}))
vi.mock('../lib/api', () => ({ api: mocks.api, isAbortError: (error: Error) => error.name === 'AbortError' }))

function payload(user = 7, org = 'org-a'): UserUsage {
  return {
    org_key: org, user_id: user, limit_scope: 'organization_user',
    generated_at: '2026-11-13T01:00:00Z',
    items: USER_USAGE_METERS.map(key => ({
      key, unit: key.endsWith('_bytes') ? 'bytes' : key === 'ai_tokens' ? 'tokens' : 'count',
      used: 2, known_used: 2, status: 'complete', reason: '',
      limit: -1, limit_source: 'default_unlimited',
    })),
  }
}
const user = ref({ id: 7, access_profile: { org_key: 'org-a' } })
const wrappers: ReturnType<typeof mount>[] = []
function render(props = {}) {
  const wrapper = mount(UserResourceUsage, {
    props,
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.auth.currentUser = user
  user.value = { id: 7, access_profile: { org_key: 'org-a' } }
  mocks.org.mockReturnValue('org-a')
  mocks.api.mockResolvedValue(payload())
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('organization-user resource usage', () => {
  it('loads self without a subscription permission and shows Unlimited without percentages', async () => {
    const wrapper = render()
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/subscription/my-usage/', { signal: expect.any(AbortSignal) })
    expect(wrapper.findAll('[data-meter]')).toHaveLength(USER_USAGE_METERS.length)
    expect(wrapper.text()).toContain('Unlimited')
    expect(wrapper.text()).not.toContain('%')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
  })

  it('loads member by user ID, not membership ID', async () => {
    mocks.api.mockResolvedValue(payload(19))
    const wrapper = render({ userId: 19 })
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/subscription/member-usage/19/', { signal: expect.any(AbortSignal) })
    expect(wrapper.findAll('[data-meter]')).toHaveLength(USER_USAGE_METERS.length)
  })

  it('keeps unknown and partial usage distinct from measured zero', async () => {
    const response = payload()
    Object.assign(response.items.find(i => i.key === 'max_storage_bytes')!, {
      used: null, known_used: 0, status: 'unknown', reason: 'storage_attribution',
    })
    Object.assign(response.items.find(i => i.key === 'ai_tokens')!, {
      used: null, known_used: 123, status: 'partial', reason: 'unattributed_history',
    })
    mocks.api.mockResolvedValue(response)
    const wrapper = render()
    await flushPromises()
    expect(wrapper.get('[data-meter="max_storage_bytes"] .user-resource-usage__value').text()).toBe('—')
    expect(wrapper.get('[data-meter="max_storage_bytes"]').text()).not.toContain(en.accountUsage.reasons.storage_attribution)
    expect(wrapper.find('[data-meter="max_storage_bytes"] .user-resource-usage__hint').exists()).toBe(false)
    expect(wrapper.get('[data-meter="ai_tokens"]').text()).toContain('Known usage: 123.')
  })

  it('displays access denied rather than fake zero usage and supports retry', async () => {
    mocks.api.mockRejectedValueOnce({ status: 403 })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain(en.accountUsage.denied)
    expect(wrapper.find('[data-meter]').exists()).toBe(false)
    await wrapper.get('.user-resource-usage__retry').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-meter]').exists()).toBe(true)
  })

  it('aborts and ignores stale member responses, including failures', async () => {
    let resolve!: (value: UserUsage) => void
    mocks.api.mockReturnValueOnce(new Promise(finish => { resolve = finish }))
    const wrapper = render({ userId: 8 })
    const signal = mocks.api.mock.calls[0][1].signal as AbortSignal
    mocks.api.mockResolvedValue(payload(9))
    await wrapper.setProps({ userId: 9 })
    await flushPromises()
    expect(signal.aborted).toBe(true)
    resolve(payload(8))
    await flushPromises()
    expect(wrapper.text()).not.toContain(en.accountUsage.loadFailed)
    expect(wrapper.findAll('[data-meter]')).toHaveLength(USER_USAGE_METERS.length)
  })

  it('clears previous usage and rejects late responses on organization and account changes', async () => {
    const wrapper = render()
    await flushPromises()
    let finish!: (value: UserUsage) => void
    mocks.api.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    mocks.org.mockReturnValue('org-b')
    user.value = { id: 7, access_profile: { org_key: 'org-b' } }
    await flushPromises()
    expect(wrapper.find('[data-meter]').exists()).toBe(false)
    mocks.api.mockResolvedValue(payload(18, 'org-b'))
    user.value = { id: 18, access_profile: { org_key: 'org-b' } }
    await flushPromises()
    finish(payload(7, 'org-b'))
    await flushPromises()
    expect(wrapper.text()).not.toContain(en.accountUsage.loadFailed)
    expect(wrapper.findAll('[data-meter]')).toHaveLength(USER_USAGE_METERS.length)
  })

  it('does not request inactive panes, aborts on close, and reloads on reopening', async () => {
    const wrapper = render({ active: false })
    await flushPromises()
    expect(mocks.api).not.toHaveBeenCalled()
    await wrapper.setProps({ active: true })
    await flushPromises()
    const signal = mocks.api.mock.calls[0][1].signal
    await wrapper.setProps({ active: false })
    expect(signal.aborted).toBe(true)
    expect(wrapper.find('[data-meter]').exists()).toBe(false)
    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    expect(mocks.api.mock.calls[1][1].signal.aborted).toBe(true)
  })

  it('rejects malformed and cross-context responses rather than silently omitting meters', async () => {
    mocks.api.mockResolvedValue({ ...payload(), items: [] })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain(en.accountUsage.loadFailed)
    expect(validUserUsage(payload(), 'foreign', 7)).toBe(false)
    expect(validUserUsage(payload(), 'org-a', 8)).toBe(false)
    for (const bad of [NaN, Infinity, -1, null, '2']) {
      // Each invalid measurement must fail without crashing the view.
      const data = payload()
      Object.assign(data.items[0], { used: bad })
      expect(validUserUsage(data, 'org-a', 7)).toBe(false)
    }
    const duplicate = payload()
    duplicate.items[1] = { ...duplicate.items[0] }
    expect(validUserUsage(duplicate, 'org-a', 7)).toBe(false)
  })
})
