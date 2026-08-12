// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { BackupSourceSnapshot } from '../lib/protectionBackupConfigApi'
import { useKnowledgeSourceForm } from './useKnowledgeSourceForm'

const mocks = vi.hoisted(() => ({
  browseCopilotSnapshotDirectory: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    warning: mocks.warning,
    error: mocks.error,
    success: mocks.success,
  },
}))

vi.mock('../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

vi.mock('../lib/lensApi', () => ({
  browseCopilotSnapshotDirectory: mocks.browseCopilotSnapshotDirectory,
  browseGatewayDirectory: vi.fn(),
  createKnowledgeSource: vi.fn(),
  fetchKnowledgeSource: vi.fn(),
  listLensGateways: vi.fn().mockResolvedValue([]),
  patchKnowledgeSource: vi.fn(),
}))

vi.mock('../lib/protectionBackupConfigApi', () => ({
  getBackupSourceSnapshot: vi.fn(),
  listBackupSourceSnapshots: vi.fn().mockResolvedValue({ results: [] }),
}))

type KnowledgeSourceForm = ReturnType<typeof useKnowledgeSourceForm>

function snapshotFixture(): BackupSourceSnapshot {
  return {
    id: 71,
    snapshot_uid: 'snapshot-71',
    source_type: 'agent',
    source_ref_id: 9,
    source_display_name: 'test-source',
    backup_config_id: 42,
    backup_config_name: 'test-config',
    repository_id: 3,
    repository_display_name: 'test-repository',
    repository_endpoint_type: 'internal',
    repository_endpoint: 'local',
    task_id: 4,
    task_uuid: 'task-4',
    trigger_type: 'manual',
    status: 'available',
    created_at: '2026-07-31T00:00:00Z',
    directory_count: 1,
    successful_directory_count: 1,
    failed_directory_count: 0,
    kopia_snapshot_count: 1,
    total_size_bytes: 1024,
    file_count: 2,
    dir_count: 2,
    directories: [{
      id: 31,
      backup_config_dir_id: 8,
      source_path: '/root/datatest',
      path_type: 'directory',
      display_name: 'datatest',
      repository_id: 3,
      status: 'available',
      created_at: '2026-07-31T00:00:00Z',
      size_bytes: 1024,
      file_count: 2,
      dir_count: 2,
    }],
  }
}

function mountForm(): { form: KnowledgeSourceForm; wrapper: VueWrapper } {
  let form!: KnowledgeSourceForm
  const wrapper = mount(defineComponent({
    setup() {
      form = useKnowledgeSourceForm(ref(1), ref('backup_source'))
      return () => h('div')
    },
  }))
  const snapshot = snapshotFixture()
  form.snapshots.value = [snapshot]
  form.selectedBackupConfigId.value = snapshot.backup_config_id
  form.snapshotPickerValue.value = snapshot.id
  form.snapshotDetail.value = snapshot
  return { form, wrapper }
}

describe('knowledge source backup scope validation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mocks.browseCopilotSnapshotDirectory.mockResolvedValue({ entries: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not warn when blur is caused by opening the scope picker', async () => {
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      const validation = form.validateBackupScopeEntryOnBlur(entryId)
      form.setBackupScopePickerOpen(entryId, true)

      await vi.runAllTimersAsync()

      await expect(validation).resolves.toBe(false)
      expect(mocks.warning).not.toHaveBeenCalled()
      expect(mocks.browseCopilotSnapshotDirectory).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('accepts a tree selection that lands after the input blur event', async () => {
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      const validation = form.validateBackupScopeEntryOnBlur(entryId)
      form.pickBackupScopeForEntry(entryId, {
        id: '31:dir:/root/datatest',
        label: 'datatest',
        path: '/root/datatest',
        type: 'dir',
        directoryId: 31,
        isLeaf: false,
      })

      await vi.runAllTimersAsync()

      await expect(validation).resolves.toBe(true)
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest',
        directoryId: 31,
        pathType: 'dir',
      })
      expect(mocks.warning).not.toHaveBeenCalled()
      expect(mocks.browseCopilotSnapshotDirectory).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('still validates a manually entered source path after a real blur', async () => {
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
      const validation = form.validateBackupScopeEntryOnBlur(entryId)

      await vi.runAllTimersAsync()

      await expect(validation).resolves.toBe(true)
      expect(mocks.browseCopilotSnapshotDirectory).toHaveBeenCalledWith(31, {
        path: 'docs',
        limit: 1,
      }, expect.any(AbortSignal))
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest/docs',
        directoryId: 31,
      })
    } finally {
      wrapper.unmount()
    }
  })

  it('validates a manual path when the picker closes without a selection', async () => {
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
      const blurValidation = form.validateBackupScopeEntryOnBlur(entryId)
      form.setBackupScopePickerOpen(entryId, true)

      await vi.runAllTimersAsync()
      await expect(blurValidation).resolves.toBe(false)
      expect(mocks.browseCopilotSnapshotDirectory).not.toHaveBeenCalled()

      form.setBackupScopePickerOpen(entryId, false)
      await vi.runAllTimersAsync()
      await flushPromises()

      expect(mocks.browseCopilotSnapshotDirectory).toHaveBeenCalledWith(31, {
        path: 'docs',
        limit: 1,
      }, expect.any(AbortSignal))
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest/docs',
        directoryId: 31,
      })
    } finally {
      wrapper.unmount()
    }
  })

  it('deduplicates picker-close and blur validation from the same outside click', async () => {
    let resolveBrowse!: (value: { entries: never[] }) => void
    mocks.browseCopilotSnapshotDirectory.mockReturnValueOnce(
      new Promise((resolve) => { resolveBrowse = resolve }),
    )
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
      form.setBackupScopePickerOpen(entryId, true)

      const blurValidation = form.validateBackupScopeEntryOnBlur(entryId)
      form.setBackupScopePickerOpen(entryId, false)
      expect(mocks.browseCopilotSnapshotDirectory).not.toHaveBeenCalled()

      await vi.runAllTimersAsync()
      expect(mocks.browseCopilotSnapshotDirectory).toHaveBeenCalledTimes(1)
      resolveBrowse({ entries: [] })

      await expect(blurValidation).resolves.toBe(false)
      await flushPromises()
      expect(mocks.browseCopilotSnapshotDirectory).toHaveBeenCalledTimes(1)
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest/docs',
        directoryId: 31,
      })
    } finally {
      wrapper.unmount()
    }
  })

  it('deduplicates the same validation when picker-close arrives before blur', async () => {
    let resolveBrowse!: (value: { entries: never[] }) => void
    mocks.browseCopilotSnapshotDirectory.mockReturnValueOnce(
      new Promise((resolve) => { resolveBrowse = resolve }),
    )
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
      form.setBackupScopePickerOpen(entryId, true)

      form.setBackupScopePickerOpen(entryId, false)
      const blurValidation = form.validateBackupScopeEntryOnBlur(entryId)
      expect(mocks.browseCopilotSnapshotDirectory).not.toHaveBeenCalled()

      await vi.runAllTimersAsync()
      expect(mocks.browseCopilotSnapshotDirectory).toHaveBeenCalledTimes(1)
      resolveBrowse({ entries: [] })

      await expect(blurValidation).resolves.toBe(true)
      expect(mocks.browseCopilotSnapshotDirectory).toHaveBeenCalledTimes(1)
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest/docs',
        directoryId: 31,
      })
    } finally {
      wrapper.unmount()
    }
  })

  it('does not let an older async validation overwrite a newer tree selection', async () => {
    let resolveBrowse!: (value: { entries: never[] }) => void
    mocks.browseCopilotSnapshotDirectory.mockReturnValueOnce(
      new Promise((resolve) => { resolveBrowse = resolve }),
    )
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      form.updateBackupScopeEntryInput(entryId, '/root/datatest/old')
      const staleValidation = form.validateBackupScopeEntry(entryId)
      form.pickBackupScopeForEntry(entryId, {
        id: '31:dir:/root/datatest/new',
        label: 'new',
        path: '/root/datatest/new',
        type: 'dir',
        directoryId: 31,
        isLeaf: false,
      })
      resolveBrowse({ entries: [] })

      await expect(staleValidation).resolves.toBe(false)
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest/new',
        directoryId: 31,
        pathType: 'dir',
      })
      expect(mocks.error).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('lets only the latest request report the result for an unchanged path', async () => {
    let rejectFirst!: (reason: Error) => void
    let resolveSecond!: (value: { entries: never[] }) => void
    mocks.browseCopilotSnapshotDirectory
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectFirst = reject }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))
    const { form, wrapper } = mountForm()
    try {
      const entryId = form.backupScopeEntries.value[0].id
      form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
      const firstValidation = form.validateBackupScopeEntry(entryId)
      const secondValidation = form.validateBackupScopeEntry(entryId)

      resolveSecond({ entries: [] })
      await expect(secondValidation).resolves.toBe(true)
      rejectFirst(new Error('stale request failed'))

      await expect(firstValidation).resolves.toBe(false)
      expect(form.backupScopeEntries.value[0]).toMatchObject({
        path: '/root/datatest/docs',
        directoryId: 31,
      })
      expect(mocks.error).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('cancels a scheduled blur validation when the form unmounts', async () => {
    const { form, wrapper } = mountForm()
    const entryId = form.backupScopeEntries.value[0].id
    form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
    const validation = form.validateBackupScopeEntryOnBlur(entryId)

    wrapper.unmount()
    await vi.runAllTimersAsync()

    await expect(validation).resolves.toBe(false)
    expect(mocks.browseCopilotSnapshotDirectory).not.toHaveBeenCalled()
    expect(mocks.warning).not.toHaveBeenCalled()
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('ignores a successful validation result after the form unmounts', async () => {
    let resolveBrowse!: (value: { entries: never[] }) => void
    mocks.browseCopilotSnapshotDirectory.mockReturnValueOnce(
      new Promise((resolve) => { resolveBrowse = resolve }),
    )
    const { form, wrapper } = mountForm()
    const entryId = form.backupScopeEntries.value[0].id
    form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
    const validation = form.validateBackupScopeEntry(entryId)

    wrapper.unmount()
    resolveBrowse({ entries: [] })

    await expect(validation).resolves.toBe(false)
    expect(form.backupScopeEntries.value[0]).toMatchObject({
      path: '/root/datatest/docs',
      directoryId: null,
    })
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('suppresses a failed validation result after the form unmounts', async () => {
    let rejectBrowse!: (reason: Error) => void
    mocks.browseCopilotSnapshotDirectory.mockReturnValueOnce(
      new Promise((_resolve, reject) => { rejectBrowse = reject }),
    )
    const { form, wrapper } = mountForm()
    const entryId = form.backupScopeEntries.value[0].id
    form.updateBackupScopeEntryInput(entryId, '/root/datatest/docs')
    const validation = form.validateBackupScopeEntry(entryId)

    wrapper.unmount()
    rejectBrowse(new Error('request completed after navigation'))

    await expect(validation).resolves.toBe(false)
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('treats an aborted directory browse as normal form disposal', async () => {
    mocks.browseCopilotSnapshotDirectory.mockImplementationOnce(
      (_directoryId, _params, signal: AbortSignal) => new Promise((_resolve, reject) => {
        signal.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        )
      }),
    )
    const { form, wrapper } = mountForm()
    const resolvedNodes: unknown[][] = []
    const browse = form.loadBackupScopePickerNode(
      {
        level: 1,
        data: {
          id: '31:dir:/root/datatest',
          label: 'datatest',
          path: '/root/datatest',
          type: 'dir',
          directoryId: 31,
          browsePath: '',
          sourceRootPath: '/root/datatest',
          isLeaf: false,
        },
      },
      (nodes) => resolvedNodes.push(nodes),
    )

    wrapper.unmount()
    await browse

    expect(resolvedNodes).toEqual([[]])
    expect(mocks.error).not.toHaveBeenCalled()
  })
})
