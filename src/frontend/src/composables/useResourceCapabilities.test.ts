// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref, type Ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentUser } from './useAuth'
import { useResourceCapabilities, type ResourceReference } from './useResourceCapabilities'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../lib/api', () => ({ api: mocks.api }))
vi.mock('./useAuth', async () => {
  const { ref } = await import('vue')
  const currentUser = ref()
  return {
    currentUser,
    getEffectiveOrgKey: () => localStorage.getItem('hfl_org_key')
      || currentUser.value?.access_profile?.org_key || '',
  }
})

const wrappers: ReturnType<typeof mount>[] = []
const config: ResourceReference = { resource_type: 'backup_config', resource_id: 85 }
const secondConfig: ResourceReference = { resource_type: 'backup_config', resource_id: 86 }

function user(id = 1, org = 'org-a') {
  return {
    id, username: 'tester', email: 'tester@example.test',
    access_profile: {
      org_key: org, role: 'operator',
      permissions: ['protection.restore_plans.run', 'protection.backup_configurations.view'],
      group_permissions: ['operations.tasks.view'],
      visible_features: ['backup'], available_platforms: [], landing_path: '/',
    },
  }
}

function deferred<T = unknown>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function response(resources: ResourceReference[] = [config], allowed = true, org = 'org-a') {
  return {
    organization_key: org,
    resources: resources.map((row) => ({ ...row, run_restore: allowed, manage: allowed })),
    create: { backup_config: allowed },
  }
}

function render(references: Ref<ResourceReference[]> = ref([config])) {
  let permissions!: ReturnType<typeof useResourceCapabilities>
  const wrapper = mount(defineComponent({
    setup() {
      permissions = useResourceCapabilities(references)
      return { permitted: permissions.permitted }
    },
    template: '<button :disabled="!permitted(\'backup_config\', 85, \'run_restore\')">Restore</button>',
  }))
  wrappers.push(wrapper)
  return { wrapper, permissions, references }
}

function requestedResources(index: number): ResourceReference[] {
  return JSON.parse(mocks.api.mock.calls[index]![1].body).resources
}

describe('useResourceCapabilities', () => {
  beforeEach(() => {
    mocks.api.mockReset()
    localStorage.clear()
    currentUser.value = user()
    mocks.api.mockImplementation((_path: string, init: RequestInit) => Promise.resolve(
      response(JSON.parse(init.body as string).resources),
    ))
  })

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
    vi.restoreAllMocks()
  })

  it('fails closed until the first capability response succeeds', async () => {
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    const { wrapper, permissions } = render()
    expect(permissions.loading.value).toBe(true)
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(permissions.canCreate('backup_config')).toBe(false)
    await flushPromises()
    expect(requestedResources(0)).toEqual([config])
    request.resolve({ code: 0, data: response() })
    await flushPromises()
    expect(permissions.loading.value).toBe(false)
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(permissions.canCreate('backup_config')).toBe(true)
  })

  it('does not refetch on repeated equivalent session probes or reordered permission arrays', async () => {
    const { wrapper, permissions } = render()
    await flushPromises()
    for (let cycle = 0; cycle < 3; cycle++) {
      const next = user()
      next.access_profile.permissions.reverse()
      currentUser.value = next
      await flushPromises()
      expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
      expect(permissions.loading.value).toBe(false)
    }
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })

  it('normalizes duplicate/reordered resource ids and numeric/string representations', async () => {
    const { references, permissions } = render(ref([config, secondConfig]))
    await flushPromises()
    references.value = [secondConfig, { ...config, resource_id: '85' }, secondConfig]
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
  })

  it('retains confirmed hints during a background refresh and immediately applies denial', async () => {
    const { wrapper, permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    const refresh = permissions.refresh()
    await flushPromises()
    expect(permissions.loading.value).toBe(true)
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(permissions.canCreate('backup_config')).toBe(true)
    request.resolve(response([config], false))
    await refresh
    await nextTick()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(permissions.canCreate('backup_config')).toBe(false)
  })

  it('coalesces manual and focus refreshes for the same in-flight scope', async () => {
    const { permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    const first = permissions.refresh()
    const second = permissions.refresh()
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(first).toBe(second)
    expect(mocks.api).toHaveBeenCalledTimes(2)
    request.resolve(response())
    await first
  })

  it('does not merge a full focus refresh into a narrower incremental request', async () => {
    const { references, permissions } = render()
    await flushPromises()
    const added = deferred()
    mocks.api.mockReturnValueOnce(added.promise)
    references.value = [config, secondConfig]
    await flushPromises()
    expect(requestedResources(1)).toEqual([secondConfig])
    const addedSignal = mocks.api.mock.calls[1]![1].signal as AbortSignal

    const full = deferred()
    mocks.api.mockReturnValueOnce(full.promise)
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)
    expect(requestedResources(2)).toEqual([config, secondConfig])
    expect(addedSignal.aborted).toBe(true)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)

    added.resolve(response([secondConfig]))
    await flushPromises()
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
    expect(permissions.loading.value).toBe(true)
    full.resolve(response([config, secondConfig], false))
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
    expect(permissions.loading.value).toBe(false)
  })

  it('continues a background recheck for retained rows when the resource list narrows', async () => {
    const { references, permissions } = render(ref([config, secondConfig]))
    await flushPromises()
    const previous = deferred()
    mocks.api.mockReturnValueOnce(previous.promise)
    const refresh = permissions.refresh()
    await flushPromises()
    const previousSignal = mocks.api.mock.calls[1]![1].signal as AbortSignal

    const current = deferred()
    mocks.api.mockReturnValueOnce(current.promise)
    references.value = [config]
    await flushPromises()
    expect(previousSignal.aborted).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(3)
    expect(requestedResources(2)).toEqual([config])
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)

    previous.resolve(response([config, secondConfig]))
    await refresh
    expect(permissions.loading.value).toBe(true)
    current.resolve(response([config], false))
    await flushPromises()
    expect(permissions.loading.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it('still rechecks creation hints when a background recheck loses all resource rows', async () => {
    const { references, permissions } = render()
    await flushPromises()
    const previous = deferred()
    mocks.api.mockReturnValueOnce(previous.promise)
    const refresh = permissions.refresh()
    await flushPromises()
    mocks.api.mockResolvedValueOnce(response([], false))
    references.value = []
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)
    expect(requestedResources(2)).toEqual([])
    expect(permissions.canCreate('backup_config')).toBe(false)
    previous.resolve(response())
    await refresh
    expect(permissions.canCreate('backup_config')).toBe(false)
  })

  it('immediately invalidates hints after an authoritative action rejection', async () => {
    const { wrapper, permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    const refresh = permissions.refresh({ invalidate: true })
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)
    await nextTick()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    request.resolve(response([config], false))
    await refresh
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it('invalidates queried grants on failure and supports retry without falling back to old allows', async () => {
    const { permissions } = render()
    await flushPromises()
    mocks.api.mockRejectedValueOnce(new Error('offline'))
    await permissions.refresh()
    expect(permissions.failed.value).toBe(true)
    expect(permissions.loading.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)
    await permissions.refresh()
    expect(permissions.failed.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
  })

  it('retries failed checks when reloaded list data has the same resource ids', async () => {
    mocks.api.mockRejectedValueOnce(new Error('temporary outage'))
    const { references, wrapper, permissions } = render()
    await flushPromises()
    expect(permissions.failed.value).toBe(true)
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    references.value = [{ ...config }]
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(requestedResources(1)).toEqual([config])
    expect(permissions.failed.value).toBe(false)
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })

  it('does not loop or retry healthy same-id reloads after a failed-check retry', async () => {
    mocks.api.mockRejectedValueOnce(new Error('offline'))
    const { references, permissions } = render()
    await flushPromises()
    mocks.api.mockRejectedValueOnce(new Error('still offline'))
    references.value = [{ ...config }]
    await flushPromises()
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(permissions.failed.value).toBe(true)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)

    references.value = [{ ...config }]
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)
    expect(permissions.failed.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    references.value = [{ ...config }]
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)
  })

  it('treats wrong-organization responses as failed checks', async () => {
    const { permissions } = render()
    await flushPromises()
    mocks.api.mockResolvedValueOnce(response([config], true, 'org-b'))
    await permissions.refresh()
    expect(permissions.failed.value).toBe(true)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it('removes omitted grants and ignores unsolicited resource grants', async () => {
    const { permissions } = render()
    await flushPromises()
    mocks.api.mockResolvedValueOnce(response([secondConfig]))
    await permissions.refresh()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
  })

  it('invalidates a known role change before the watcher flushes', async () => {
    const { permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    currentUser.value!.access_profile!.role = 'auditor'
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)
    await flushPromises()
    expect(permissions.loading.value).toBe(true)
    request.resolve(response([config], false))
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it.each(['permissions', 'group_permissions'] as const)('observes in-place changes to %s', async (field) => {
    const { permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    currentUser.value!.access_profile![field]!.push('protection.backup_configurations.delete')
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    request.resolve(response())
    await flushPromises()
  })

  it('clears grants on logout and does not issue a new unauthenticated request', async () => {
    const { permissions } = render()
    await flushPromises()
    currentUser.value = null
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    await flushPromises()
    await permissions.refresh()
    expect(permissions.canCreate('backup_config')).toBe(false)
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })

  it('does not reuse grants when another user enters the same organization', async () => {
    const { permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    currentUser.value = user(2)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    await flushPromises()
    request.resolve(response([config], false))
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it('aborts obsolete requests and ignores late grants after an organization switch', async () => {
    const old = deferred()
    const next = deferred()
    mocks.api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
    const { permissions } = render()
    await flushPromises()
    const signal = mocks.api.mock.calls[0]![1].signal as AbortSignal
    // Match setUser: profile first, stored organization second, in the same tick.
    currentUser.value = user(1, 'org-b')
    localStorage.setItem('hfl_org_key', 'org-b')
    await flushPromises()
    expect(signal.aborted).toBe(true)
    next.resolve(response([config], false, 'org-b'))
    await flushPromises()
    old.resolve(response())
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.loading.value).toBe(false)
  })

  it('guards stored organization changes even before a reactive update', async () => {
    const { permissions } = render()
    await flushPromises()
    localStorage.setItem('hfl_org_key', 'org-b')
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)
    mocks.api.mockResolvedValueOnce(response([config], false, 'org-b'))
    await permissions.refresh()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it('loads only added resources without disabling unchanged resources', async () => {
    const { references, permissions } = render()
    await flushPromises()
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    references.value = [config, secondConfig]
    await flushPromises()
    expect(requestedResources(1)).toEqual([secondConfig])
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
    request.reject(new Error('offline'))
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
  })

  it('prunes removed resources without refetching unchanged grants', async () => {
    const { references, permissions } = render(ref([config, secondConfig]))
    await flushPromises()
    references.value = [config]
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })

  it('ignores late responses for a previous page', async () => {
    const old = deferred()
    const next = deferred()
    mocks.api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
    const { references, permissions } = render()
    await flushPromises()
    references.value = [secondConfig]
    await flushPromises()
    next.resolve(response([secondConfig], false))
    await flushPromises()
    old.resolve(response())
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)
  })

  it('ignores both stale grants and stale failures after switching away and back to the same ids', async () => {
    const { references, permissions } = render()
    await flushPromises()
    const old = deferred()
    mocks.api.mockReturnValueOnce(old.promise)
    const refresh = permissions.refresh()
    await flushPromises()

    const middle = deferred()
    mocks.api.mockReturnValueOnce(middle.promise)
    references.value = [secondConfig]
    await flushPromises()
    const current = deferred()
    mocks.api.mockReturnValueOnce(current.promise)
    references.value = [config]
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(4)

    old.resolve(response())
    middle.reject(new Error('obsolete page failed'))
    await refresh
    await flushPromises()
    expect(permissions.loading.value).toBe(true)
    expect(permissions.failed.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)

    current.resolve(response([config], false))
    await flushPromises()
    expect(permissions.loading.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })

  it('does not restore stale allows when an invalidating recheck supersedes a background request', async () => {
    const { permissions } = render()
    await flushPromises()
    const old = deferred()
    mocks.api.mockReturnValueOnce(old.promise)
    const background = permissions.refresh()
    await flushPromises()
    const current = deferred()
    mocks.api.mockReturnValueOnce(current.promise)
    const invalidating = permissions.refresh({ invalidate: true })
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)

    old.resolve(response())
    await background
    expect(permissions.loading.value).toBe(true)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)

    current.reject(new Error('recheck failed'))
    await invalidating
    expect(permissions.failed.value).toBe(true)
    expect(permissions.loading.value).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)
  })

  it('uses bounded batches and publishes results only after every batch succeeds', async () => {
    const refs = Array.from({ length: 201 }, (_, index): ResourceReference => ({
      resource_type: 'backup_config', resource_id: index + 1,
    }))
    const last = deferred()
    mocks.api.mockImplementationOnce((_path: string, init: RequestInit) => Promise.resolve(
      response(JSON.parse(init.body as string).resources),
    )).mockReturnValueOnce(last.promise)
    const { permissions } = render(ref(refs))
    await flushPromises()
    expect(requestedResources(0)).toHaveLength(200)
    expect(requestedResources(1)).toHaveLength(1)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    last.resolve(response(requestedResources(1)))
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    expect(permissions.permitted('backup_config', 201, 'run_restore')).toBe(true)
  })

  it('queries creation hints even when the resource page is empty', async () => {
    const { permissions } = render(ref([]))
    await flushPromises()
    expect(requestedResources(0)).toEqual([])
    expect(permissions.canCreate('backup_config')).toBe(true)
  })

  it('recovers creation hints after reloading an empty page whose initial check failed', async () => {
    mocks.api.mockRejectedValueOnce(new Error('temporary outage'))
    const { references, permissions } = render(ref([]))
    await flushPromises()
    expect(permissions.failed.value).toBe(true)
    expect(permissions.canCreate('backup_config')).toBe(false)
    references.value = []
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(requestedResources(1)).toEqual([])
    expect(permissions.failed.value).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(true)
  })

  it('fails closed for the whole queried batch set when a later batch fails', async () => {
    const refs = Array.from({ length: 201 }, (_, index): ResourceReference => ({
      resource_type: 'backup_config', resource_id: index + 1,
    }))
    const { permissions } = render(ref(refs))
    await flushPromises()
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    mocks.api.mockImplementationOnce((_path: string, init: RequestInit) => Promise.resolve(
      response(JSON.parse(init.body as string).resources),
    )).mockRejectedValueOnce(new Error('second batch failed'))
    await permissions.refresh()
    expect(permissions.failed.value).toBe(true)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(permissions.permitted('backup_config', 201, 'run_restore')).toBe(false)
    expect(permissions.canCreate('backup_config')).toBe(false)
  })

  it('does not continue obsolete batches after a user switch', async () => {
    const refs = Array.from({ length: 201 }, (_, index): ResourceReference => ({
      resource_type: 'backup_config', resource_id: index + 1,
    }))
    const old = deferred()
    mocks.api.mockReturnValueOnce(old.promise)
    const { permissions } = render(ref(refs))
    await flushPromises()
    currentUser.value = user(2)
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)
    old.resolve(response(requestedResources(0)))
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(3)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
  })

  it('aborts on unmount and removes the focus listener', async () => {
    const request = deferred()
    mocks.api.mockReturnValueOnce(request.promise)
    const { wrapper, permissions } = render()
    await flushPromises()
    const signal = mocks.api.mock.calls[0]![1].signal as AbortSignal
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    request.resolve(response())
    await flushPromises()
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
  })
})
