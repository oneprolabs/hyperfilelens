import { afterEach, describe, expect, it, vi } from 'vitest'
import { transpile } from 'typescript'
import source from './BackupCreateWizard.vue?raw'
import { createSourcePathRequests, SOURCE_PATH_WAIT_SECONDS } from '../../lib/sourcePathRequests'
import { selectBackupSourceDirectoryTreeEntries, shouldAutoExpandRefreshedDirectory } from '../../lib/backupSourceDirectoryTree'
import { isAbortError, normalizeThrownError } from '../../lib/errors'

function section(start: string, end: string) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)))
}

function harness() {
  const state = {
    sourcePathRequests: createSourcePathRequests(),
    sourcePathRequestGeneration: 0,
    sourceTreeRequestGenerations: new Map<string, number>(),
    getEffectiveOrgKey: () => 'org-1',
    createOpen: { value: true },
    createStep: { value: 0 },
    createSourceIds: { value: ['agent:1'] },
    treeRemountKey: { value: 0 },
    createSourceTreeRemountKeyBySource: {} as Record<string, number>,
    sourceRecord: () => ({ boundNodeId: 1, platform: 'windows' }),
    realSourceById: { value: new Map([['agent:1', { platform: 'windows' }]]) },
    isEditMode: { value: false },
    wizardDirEntryKey: (id: string, path: string) => `${id}||${path}`,
    manualSourcePathBySource: { 'agent:1': 'F:\\hyperfilelens\\source' } as Record<string, string>,
    manualSourcePathValidatingBySource: {} as Record<string, boolean>,
    manualSourcePathErrorBySource: {} as Record<string, string>,
    manualSourcePathErrorCodeBySource: {} as Record<string, string>,
    highlightedCreateSourceId: { value: '' },
    getBackupSourcePathInfo: vi.fn(),
    isAbsoluteSourcePath: () => true,
    addedDirBlockReason: () => '',
    isAbortError,
    normalizeThrownError,
    backupPathAccessSummaryKey: () => undefined,
    apiErrorMessageI18n: (error: unknown) => (error as Error).message || 'Access denied',
    backupPathTypeForEntry: () => 'directory',
    sourceKey: (id: string, path: string) => `${id}||${path}`,
    getSourceType: () => 'host',
    getSourceName: () => 'Windows source',
    wizardDirEntries: { value: [] as Array<{ path: string }> },
    setSourcePathType: vi.fn(),
    sourceGroupKey: () => 'agent:1',
    nasTargetModes: {} as Record<string, string>,
    refreshCreateSourceTreeBlockedState: vi.fn(),
    t: (key: string) => key,
    ElMessage: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
    directoryLoadingByKey: {} as Record<string, number>,
    refreshingSourceDirectoryByKey: {} as Record<string, boolean>,
    createSourceTreeRefs: new Map<string, ReturnType<typeof makeTree>>(),
    createSourceTreeErrorBySource: {} as Record<string, string>,
    noSourceTreeRootsBySource: {} as Record<string, boolean>,
    sourceDirectoryExpansionRevisionByKey: new Map<string, number>(),
    listBackupSourceDirectories: vi.fn(),
    selectBackupSourceDirectoryTreeEntries,
    mapSourceDirectoryEntry: (entry: { path: string }) => entry,
    SOURCE_TREE_ROOT_LIMIT: 100,
    SOURCE_TREE_CHILD_LIMIT: 500,
    sourceTreeItemsWithLoadMore: (_id: string, _path: string, page: { entries: unknown[] }) => page.entries,
    sourceTreeItemWithBlockedState: (_id: string, item: unknown) => item,
    showSourceTreeFilenamePathError: () => false,
    shouldAutoExpandRefreshedDirectory,
    syncCreateSourceTreeCheckedKeys: vi.fn(),
    nextTick: async (callback?: () => void) => callback?.(),
    SOURCE_PATH_WAIT_SECONDS,
  }
  const code = [
    section('function startSourcePathRequest(', 'function sourceDirectoryErrorMessage('),
    section('function clearManualSourcePathError(', 'function sourceNameForError('),
    section('function isManualSourcePathValidating(', 'function removeWizardDirEntry('),
    section('function cancelSourcePathRequests(', 'function startSourcePathRequest('),
    section('function sourceDirectoryErrorMessage(', 'const recoveryDirectoryCreateNameByKey'),
    section('function sourceNameForError(', 'function normalizeBackupPathType('),
    section('function setDirectoryLoading(', 'function recoveryDirectoryCreateKey('),
    section('function createSourceDirectoryLoadingKey(', 'function createRecoverySourceRootNodes('),
    section('async function listRealSourceDirChildren(', 'function isCreateSourceRowExpanded('),
    section('async function loadSourceTreeNode(', 'function onSourceDirCheckChange('),
    section('function setCreateSourceTreeRef(', 'function sourceSelectedEntries('),
  ].map((value) => transpile(value)).join('\n')
  const methods = new Function('state', `with (state) {
    ${code}
    return { addManualSourcePath, clearManualSourcePathError, cancelSourcePathRequests,
      loadSourceTreeNode, refreshCreateSourceDirectory, loadMoreSourceTreeChildren,
      reloadCreateSourceTree, setCreateSourceTreeRef };
  }`)(state) as {
    addManualSourcePath: (id: string) => Promise<void>
    clearManualSourcePathError: (id: string) => void
    cancelSourcePathRequests: () => void
    loadSourceTreeNode: (id: string, node: { level: number; data?: { path: string } }, resolve: (items: unknown[]) => void) => Promise<void>
    refreshCreateSourceDirectory: (id: string, data: { path: string }) => Promise<void>
    loadMoreSourceTreeChildren: (item: { nodeKind: string; sourceId: string; parentPath: string; path: string; nextCursor: string; loading?: boolean }) => Promise<void>
    reloadCreateSourceTree: (id: string) => void
    setCreateSourceTreeRef: (id: string, ref: unknown) => void
  }
  return { state, ...methods }
}

function makeTree() {
  const node = { expanded: false, loaded: false, updateLeafState: vi.fn(), expand: vi.fn(), collapse: vi.fn() }
  return { getNode: vi.fn(() => node), updateKeyChildren: vi.fn(), append: vi.fn(), remove: vi.fn() }
}

afterEach(() => vi.useRealTimers())

describe('backup wizard source path lifecycle', () => {
  it('waits for a single successful eleven-second validation before adding the path', async () => {
    vi.useFakeTimers()
    const h = harness()
    h.state.getBackupSourcePathInfo.mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve({
      path: 'F:\\hyperfilelens\\source', path_type: 'directory',
    }), 11_200)))
    const pending = h.addManualSourcePath('agent:1')
    await h.addManualSourcePath('agent:1')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(h.state.wizardDirEntries.value).toEqual([])
    expect(h.state.manualSourcePathValidatingBySource['agent:1']).toBe(true)
    expect(h.state.ElMessage.error).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1_200)
    await pending
    expect(h.state.getBackupSourcePathInfo).toHaveBeenCalledOnce()
    expect(h.state.getBackupSourcePathInfo).toHaveBeenCalledWith(expect.objectContaining({
      timeout: 30, include_metadata: false,
    }), expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(h.state.wizardDirEntries.value).toEqual([expect.objectContaining({ path: 'F:\\hyperfilelens\\source' })])
    expect(h.state.manualSourcePathValidatingBySource['agent:1']).toBeFalsy()
  })

  it.each(['close', 'source', 'organization', 'step', 'binding'] as const)('ignores late success after %s changes', async (change) => {
    const h = harness()
    let resolve!: (value: unknown) => void
    h.state.getBackupSourcePathInfo.mockReturnValue(new Promise((done) => { resolve = done }))
    const pending = h.addManualSourcePath('agent:1')
    if (change === 'close') h.state.createOpen.value = false
    if (change === 'source') h.state.createSourceIds.value = ['agent:2']
    if (change === 'organization') h.state.getEffectiveOrgKey = () => 'org-2'
    if (change === 'step') h.state.createStep.value = 1
    if (change === 'binding') h.state.sourceRecord = () => ({ boundNodeId: 2, platform: 'windows' })
    resolve({ path: 'F:\\hyperfilelens\\source' })
    await pending
    expect(h.state.wizardDirEntries.value).toEqual([])
    expect(h.state.ElMessage.error).not.toHaveBeenCalled()
  })

  it('does not let a canceled request clear a newer validation or report its error', async () => {
    const h = harness()
    h.state.getBackupSourcePathInfo.mockReturnValueOnce(new Promise(() => {}))
    const old = h.addManualSourcePath('agent:1')
    h.clearManualSourcePathError('agent:1')
    h.state.manualSourcePathBySource['agent:1'] = 'F:\\other'
    let resolve!: (value: unknown) => void
    h.state.getBackupSourcePathInfo.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const current = h.addManualSourcePath('agent:1')
    await old
    expect(h.state.manualSourcePathValidatingBySource['agent:1']).toBe(true)
    resolve({ path: 'F:\\other' })
    await current
    expect(h.state.wizardDirEntries.value).toEqual([expect.objectContaining({ path: 'F:\\other' })])
    expect(h.state.ElMessage.error).not.toHaveBeenCalled()
  })

  it('shows terminal timeout without adding an unverified path or dispatching retries', async () => {
    const h = harness()
    h.state.getBackupSourcePathInfo.mockRejectedValue({ errorCode: 'AGENT.TIMEOUT', status: 504 })
    await h.addManualSourcePath('agent:1')
    expect(h.state.getBackupSourcePathInfo).toHaveBeenCalledOnce()
    expect(h.state.manualSourcePathErrorCodeBySource['agent:1']).toBe('AGENT.TIMEOUT')
    expect(h.state.manualSourcePathErrorBySource['agent:1']).toBe('protection.backupsPage.sourcePathValidationTimedOut')
    expect(h.state.manualSourcePathValidatingBySource['agent:1']).toBe(false)
    expect(h.state.wizardDirEntries.value).toEqual([])
  })

  it('invalidates a closed wizard even when it is immediately reopened', async () => {
    const h = harness()
    h.state.getBackupSourcePathInfo.mockReturnValue(new Promise(() => {}))
    const pending = h.addManualSourcePath('agent:1')
    h.cancelSourcePathRequests()
    await pending
    expect(h.state.wizardDirEntries.value).toEqual([])
    expect(h.state.manualSourcePathValidatingBySource['agent:1']).toBeUndefined()
    expect(h.state.ElMessage.error).not.toHaveBeenCalled()
  })

  it('releases validation loading on the browser deadline without marking the Agent offline', async () => {
    vi.useFakeTimers()
    const h = harness()
    h.state.getBackupSourcePathInfo.mockReturnValue(new Promise(() => {}))
    const pending = h.addManualSourcePath('agent:1')
    await vi.advanceTimersByTimeAsync(40_000)
    await pending
    expect(h.state.manualSourcePathValidatingBySource['agent:1']).toBe(false)
    expect(h.state.manualSourcePathErrorBySource['agent:1']).toBe('protection.backupsPage.sourcePathValidationTimedOut')
    expect(h.state.wizardDirEntries.value).toEqual([])
    expect(h.state.getBackupSourcePathInfo).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('backup wizard directory request lifecycle', () => {
  it.each([0, 1])('uses thirty seconds for tree level %s and retains pagination parameters', async (level) => {
    const h = harness()
    const resolve = vi.fn()
    h.state.listBackupSourceDirectories.mockResolvedValue({ entries: [{ path: 'F:\\data' }], has_more: false })
    await h.loadSourceTreeNode('agent:1', { level, data: { path: 'F:\\' } }, resolve)
    expect(h.state.listBackupSourceDirectories).toHaveBeenCalledWith(expect.objectContaining({
      timeout: 30, limit: level === 0 ? 100 : 500, include_metadata: false, include_files: true,
    }), expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(resolve).toHaveBeenCalledWith([{ path: 'F:\\data' }])
    expect(h.state.directoryLoadingByKey).toEqual({})
  })

  it('keeps the new tree loading when a canceled old tree finishes', async () => {
    const h = harness()
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise(() => {}))
    const oldResolve = vi.fn()
    const old = h.loadSourceTreeNode('agent:1', { level: 0 }, oldResolve)
    h.reloadCreateSourceTree('agent:1')
    let resolve!: (value: unknown) => void
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const currentResolve = vi.fn()
    const current = h.loadSourceTreeNode('agent:1', { level: 0 }, currentResolve)
    await old
    expect(oldResolve).toHaveBeenCalledWith([])
    expect(h.state.directoryLoadingByKey['create-source:agent:1']).toBe(1)
    resolve({ entries: [{ path: 'F:\\current' }] })
    await current
    expect(currentResolve).toHaveBeenCalledWith([{ path: 'F:\\current' }])
    expect(h.state.directoryLoadingByKey).toEqual({})
    expect(h.state.ElMessage.error).not.toHaveBeenCalled()
  })

  it('balances loading counts when the same directory load is superseded', async () => {
    const h = harness()
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise(() => {}))
    const old = h.loadSourceTreeNode('agent:1', { level: 0 }, vi.fn())
    let resolve!: (value: unknown) => void
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const current = h.loadSourceTreeNode('agent:1', { level: 0 }, vi.fn())
    await old
    expect(h.state.directoryLoadingByKey['create-source:agent:1']).toBe(1)
    resolve({ entries: [] })
    await current
    expect(h.state.directoryLoadingByKey).toEqual({})
    expect(h.state.noSourceTreeRootsBySource['agent:1']).toBe(true)
  })

  it('clears directory loading after a browser deadline and allows a manual retry', async () => {
    vi.useFakeTimers()
    const h = harness()
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise(() => {}))
    const pending = h.loadSourceTreeNode('agent:1', { level: 0 }, vi.fn())
    await vi.advanceTimersByTimeAsync(40_000)
    await pending
    expect(h.state.directoryLoadingByKey).toEqual({})
    expect(h.state.createSourceTreeErrorBySource['agent:1']).toBe('protection.backupsPage.sourceDirectoryTimedOut')
    expect(h.state.listBackupSourceDirectories).toHaveBeenCalledOnce()
    h.state.listBackupSourceDirectories.mockResolvedValueOnce({ entries: [{ path: 'F:\\' }] })
    const resolve = vi.fn()
    await h.loadSourceTreeNode('agent:1', { level: 0 }, resolve)
    expect(resolve).toHaveBeenCalledWith([{ path: 'F:\\' }])
    expect(h.state.createSourceTreeErrorBySource['agent:1']).toBe('')
    expect(h.state.directoryLoadingByKey).toEqual({})
  })

  it('ignores late directory results before updating the path-type cache', async () => {
    const h = harness()
    let resolve!: (value: unknown) => void
    h.state.listBackupSourceDirectories.mockReturnValue(new Promise((done) => { resolve = done }))
    const callback = vi.fn()
    const pending = h.loadSourceTreeNode('agent:1', { level: 0 }, callback)
    h.state.getEffectiveOrgKey = () => 'another-org'
    resolve({ entries: [{ path: 'F:\\stale' }] })
    await pending
    expect(callback).toHaveBeenCalledWith([])
    expect(h.state.setSourcePathType).not.toHaveBeenCalled()
    expect(h.state.directoryLoadingByKey).toEqual({})
    expect(h.state.ElMessage.error).not.toHaveBeenCalled()
  })

  it('does not let an old refresh update a remounted tree or clear its refresh flag', async () => {
    const h = harness()
    const oldTree = makeTree()
    h.state.createSourceTreeRefs.set('agent:1', oldTree)
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise(() => {}))
    const old = h.refreshCreateSourceDirectory('agent:1', { path: 'F:\\data' })
    h.setCreateSourceTreeRef('agent:1', null)
    const currentTree = makeTree()
    h.setCreateSourceTreeRef('agent:1', currentTree)
    let resolve!: (value: unknown) => void
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const current = h.refreshCreateSourceDirectory('agent:1', { path: 'F:\\data' })
    await old
    expect(h.state.refreshingSourceDirectoryByKey['agent:1:F:\\data']).toBe(true)
    expect(oldTree.updateKeyChildren).not.toHaveBeenCalled()
    resolve({ entries: [] })
    await current
    expect(currentTree.updateKeyChildren).toHaveBeenCalledWith('F:\\data', [])
    expect(h.state.ElMessage.info).toHaveBeenCalledOnce()
    expect(h.state.refreshingSourceDirectoryByKey).toEqual({})
  })

  it('retains child pagination and suppresses duplicate load-more clicks', async () => {
    const h = harness()
    const tree = makeTree()
    h.state.createSourceTreeRefs.set('agent:1', tree)
    let resolve!: (value: unknown) => void
    h.state.listBackupSourceDirectories.mockReturnValue(new Promise((done) => { resolve = done }))
    const item = { nodeKind: 'loadMore', sourceId: 'agent:1', parentPath: 'F:\\data', path: 'more', nextCursor: 'page-2', loading: false }
    const pending = h.loadMoreSourceTreeChildren(item)
    await h.loadMoreSourceTreeChildren(item)
    resolve({ entries: [{ path: 'F:\\data\\next' }] })
    await pending
    expect(h.state.listBackupSourceDirectories).toHaveBeenCalledOnce()
    expect(h.state.listBackupSourceDirectories).toHaveBeenCalledWith(expect.objectContaining({
      cursor: 'page-2', limit: 500, timeout: 30,
    }), expect.anything())
    expect(tree.append).toHaveBeenCalledWith({ path: 'F:\\data\\next' }, expect.anything())
    expect(item.loading).toBe(false)
    expect(h.state.directoryLoadingByKey).toEqual({})
  })

  it('invalidates pending pagination before refreshing its parent', async () => {
    const h = harness()
    const tree = makeTree()
    h.state.createSourceTreeRefs.set('agent:1', tree)
    h.state.listBackupSourceDirectories.mockReturnValueOnce(new Promise(() => {}))
    const item = { nodeKind: 'loadMore', sourceId: 'agent:1', parentPath: 'F:\\data', path: 'more', nextCursor: 'page-2', loading: false }
    const old = h.loadMoreSourceTreeChildren(item)
    h.state.listBackupSourceDirectories.mockResolvedValueOnce({ entries: [{ path: 'F:\\data\\fresh' }] })
    await h.refreshCreateSourceDirectory('agent:1', { path: 'F:\\data' })
    await old
    expect(tree.append).not.toHaveBeenCalled()
    expect(tree.updateKeyChildren).toHaveBeenCalledWith('F:\\data', [{ path: 'F:\\data\\fresh' }])
    expect(h.state.setSourcePathType).toHaveBeenCalledOnce()
    expect(item.loading).toBe(false)
    expect(h.state.directoryLoadingByKey).toEqual({})
  })

  it.each([
    ['AGENT.TIMEOUT', 'Agent offline', 'protection.backupsPage.sourceDirectoryTimedOut'],
    ['AGENT.PATH_PERMISSION_DENIED', 'Permission denied', 'Permission denied'],
  ])('reports %s accurately without an automatic retry', async (errorCode, message, expected) => {
    const h = harness()
    h.state.listBackupSourceDirectories.mockRejectedValue({ errorCode, message, status: errorCode === 'AGENT.TIMEOUT' ? 504 : 403 })
    await h.loadSourceTreeNode('agent:1', { level: 0 }, vi.fn())
    expect(h.state.createSourceTreeErrorBySource['agent:1']).toBe(expected)
    expect(h.state.listBackupSourceDirectories).toHaveBeenCalledOnce()
    expect(h.state.noSourceTreeRootsBySource['agent:1']).toBe(false)
    expect(h.state.directoryLoadingByKey).toEqual({})
  })
})
