// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentUser } from './useAuth'
import { useBackupSourceCapabilities } from './useBackupSourceCapabilities'
import type { ResourceReference } from './useResourceCapabilities'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../lib/api', () => ({ api: mocks.api }))
vi.mock('./useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref(), getEffectiveOrgKey: () => 'org-a' }
})

const wrappers: ReturnType<typeof mount>[] = []

function render(retainSelection = false) {
  const sources = ref([
    { id: 'agent:99', hasSnapshot: true, restoreRunning: false },
    { id: 'nas:100', hasSnapshot: false, restoreRunning: false },
  ])
  const configs = ref([
    { id: 85, repository_id: 75, source_type: 'agent', source_ref_id: 99 },
    { id: 86, repository_id: 75, source_type: 'nas', source_ref_id: 100 },
    // A remembered configuration outside the current page must not be prefetched.
    { id: 87, repository_id: 76, source_type: 'agent', source_ref_id: 101 },
  ])
  const selectedIds = ref<string[]>([])
  const knownSources = new Map(sources.value.map((source) => [source.id, source]))
  const retainedSources = computed(() => retainSelection
    ? selectedIds.value.flatMap((id) => {
        const source = knownSources.get(id)
        return source ? [source] : []
      })
    : [])
  let permissions!: ReturnType<typeof useBackupSourceCapabilities>
  const wrapper = mount(defineComponent({
    setup() {
      permissions = useBackupSourceCapabilities(sources, configs, retainedSources)
      const configureEnabled = computed(() => selectedIds.value.length > 0 && selectedIds.value.every((id) => {
        const [kind, rawId] = id.split(':')
        return permissions.permitted(kind === 'nas' ? 'source_resource' : 'node', Number(rawId), 'manage')
      }))
      const restoreEnabled = computed(() => {
        const selected = sources.value.filter((source) => selectedIds.value.includes(source.id))
        return selected.length > 0 && selected.every((source) => {
          const config = configs.value.find((config) => `${config.source_type}:${config.source_ref_id}` === source.id)
          return source.hasSnapshot && !source.restoreRunning && config
            && permissions.permitted('backup_config', config.id, 'run_restore')
        })
      })
      return { restoreEnabled, configureEnabled }
    },
    template: `<div>
      <button :disabled="!restoreEnabled">Restore</button>
      <button data-test="configure" :disabled="!configureEnabled">Configure</button>
    </div>`,
  }))
  wrappers.push(wrapper)
  return { wrapper, sources, configs, selectedIds, permissions }
}

function requestedResources(index = 0): ResourceReference[] {
  return JSON.parse(mocks.api.mock.calls[index]![1].body).resources
}

describe('backup source toolbar capability prefetch', () => {
  beforeEach(() => {
    currentUser.value = {
      id: 1, username: 'tester', email: 'tester@example.test',
      access_profile: {
        org_key: 'org-a', role: 'operator', permissions: [], group_permissions: [],
        visible_features: ['backup'], available_platforms: [], landing_path: '/',
      },
    }
    mocks.api.mockReset()
    mocks.api.mockImplementation((_path: string, init: RequestInit) => Promise.resolve({
      organization_key: 'org-a',
      resources: JSON.parse(init.body as string).resources.map((row: ResourceReference) => ({
        ...row, run_restore: true, run_backup: true, run: true, manage: true,
      })),
      create: {},
    }))
  })
  afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()))

  it('prefetches visible hosts, NAS, configs and deduplicated repositories before selection', async () => {
    const { selectedIds } = render()
    await flushPromises()
    expect(selectedIds.value).toEqual([])
    expect(requestedResources()).toEqual([
      { resource_type: 'backup_config', resource_id: 85 },
      { resource_type: 'backup_config', resource_id: 86 },
      { resource_type: 'node', resource_id: 99 },
      { resource_type: 'repository', resource_id: 75 },
      { resource_type: 'source_resource', resource_id: 100 },
    ])
  })

  it('enables Restore on selection without another network request or loading interval', async () => {
    const { wrapper, selectedIds, permissions } = render()
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    selectedIds.value = ['agent:99']
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(permissions.loading.value).toBe(false)
    expect(mocks.api).toHaveBeenCalledTimes(1)
    selectedIds.value = []
    await flushPromises()
    selectedIds.value = ['agent:99']
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })

  it('keeps Restore enabled across equivalent session and source/config refreshes', async () => {
    const { wrapper, sources, configs, selectedIds } = render()
    await flushPromises()
    selectedIds.value = ['agent:99']
    await flushPromises()
    currentUser.value = { ...currentUser.value! }
    sources.value = sources.value.map((source) => ({ ...source }))
    configs.value = configs.value.map((config) => ({ ...config }))
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })

  it('recovers a selected source after retrying a failed prefetch with the same page data', async () => {
    mocks.api.mockRejectedValueOnce(new Error('temporary outage'))
    const { wrapper, sources, configs, selectedIds, permissions } = render()
    selectedIds.value = ['agent:99']
    await flushPromises()
    expect(permissions.failed.value).toBe(true)
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()

    sources.value = sources.value.map((source) => ({ ...source }))
    configs.value = configs.value.map((config) => ({ ...config }))
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(2)
    expect(permissions.failed.value).toBe(false)
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })

  it('revalidates same-page permissions on an explicit refresh without flickering', async () => {
    const { wrapper, selectedIds, permissions } = render()
    await flushPromises()
    selectedIds.value = ['agent:99']
    await flushPromises()
    let complete!: (value: unknown) => void
    mocks.api.mockReturnValueOnce(new Promise((resolve) => { complete = resolve }))
    const refresh = permissions.refresh()
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(requestedResources(1)).toEqual(requestedResources(0))
    complete({
      organization_key: 'org-a',
      resources: requestedResources(1).map((row) => ({ ...row, run_restore: false })),
      create: {},
    })
    await refresh
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  })

  it('does not bypass snapshot availability or active restore restrictions', async () => {
    const { wrapper, sources, selectedIds } = render()
    await flushPromises()
    selectedIds.value = ['nas:100']
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    selectedIds.value = ['agent:99']
    sources.value[0]!.restoreRunning = true
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    sources.value[0]!.restoreRunning = false
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
    expect(mocks.api).toHaveBeenCalledTimes(1)
  })

  it('prefetches the new page and does not retain resources from a previous page', async () => {
    const { sources, permissions } = render()
    await flushPromises()
    sources.value = [{ id: 'agent:101', hasSnapshot: true, restoreRunning: false }]
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    await flushPromises()
    expect(requestedResources(1)).toEqual([
      { resource_type: 'backup_config', resource_id: 87 },
      { resource_type: 'node', resource_id: 101 },
      { resource_type: 'repository', resource_id: 76 },
    ])
    expect(permissions.permitted('backup_config', 87, 'run_restore')).toBe(true)
  })

  it('keeps only explicit retained selections actionable across configuration pages', async () => {
    const { wrapper, sources, selectedIds, permissions } = render(true)
    await flushPromises()
    selectedIds.value = ['agent:99']
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-test="configure"]').attributes('disabled')).toBeUndefined()

    sources.value = [{ id: 'agent:101', hasSnapshot: true, restoreRunning: false }]
    await flushPromises()
    expect(selectedIds.value).toEqual(['agent:99'])
    expect(requestedResources(1)).toEqual([
      { resource_type: 'backup_config', resource_id: 87 },
      { resource_type: 'node', resource_id: 101 },
      { resource_type: 'repository', resource_id: 76 },
    ])
    expect(permissions.permitted('node', 99, 'manage')).toBe(true)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(true)
    expect(wrapper.get('[data-test="configure"]').attributes('disabled')).toBeUndefined()
    // An unselected previous-page NAS/config must still be removed.
    expect(permissions.permitted('source_resource', 100, 'manage')).toBe(false)
    expect(permissions.permitted('backup_config', 86, 'run_restore')).toBe(false)

    selectedIds.value = []
    await flushPromises()
    expect(permissions.permitted('node', 99, 'manage')).toBe(false)
    expect(permissions.permitted('backup_config', 85, 'run_restore')).toBe(false)
    expect(wrapper.get('[data-test="configure"]').attributes('disabled')).toBeDefined()
    expect(mocks.api).toHaveBeenCalledTimes(2)
  })

  it('does not turn a denied configuration permission into an allow by retaining the selection', async () => {
    mocks.api.mockImplementationOnce((_path: string, init: RequestInit) => Promise.resolve({
      organization_key: 'org-a',
      resources: JSON.parse(init.body as string).resources.map((row: ResourceReference) => ({
        ...row, manage: row.resource_type !== 'node' || Number(row.resource_id) !== 99,
      })),
      create: {},
    }))
    const { wrapper, sources, selectedIds, permissions } = render(true)
    await flushPromises()
    selectedIds.value = ['agent:99']
    await flushPromises()
    expect(wrapper.get('[data-test="configure"]').attributes('disabled')).toBeDefined()
    sources.value = [{ id: 'agent:101', hasSnapshot: true, restoreRunning: false }]
    await flushPromises()
    expect(permissions.permitted('node', 99, 'manage')).toBe(false)
    expect(wrapper.get('[data-test="configure"]').attributes('disabled')).toBeDefined()
  })
})
