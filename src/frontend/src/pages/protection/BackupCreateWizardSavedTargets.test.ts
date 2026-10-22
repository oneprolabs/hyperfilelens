// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { effectScope, ref } from 'vue'
import { transpile } from 'typescript'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { listBackupSelectableSources, type BackupSelectableSource } from '../../lib/sourceApi'
import { normalizeSourceIdList, isBackupSelectableId } from '../../composables/useDemoFlowStep2Sources'
import { useRestoreTargetCatalog } from '../../composables/useRestoreTargetCatalog'
import { canViewProtectionSources } from '../../lib/protectionAccess'
import { restorePlanTargetReferenceIsValid, savedRestoreTargetReference } from './lib/restorePlanEditReference'

vi.mock('../../lib/sourceApi', async load => ({
  ...await load<typeof import('../../lib/sourceApi')>(), listBackupSelectableSources: vi.fn(),
}))
const editor = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')
function body(start: string, end: string) {
  const from = editor.indexOf(start)
  const to = editor.indexOf(end, from + start.length)
  expect(from).toBeGreaterThan(-1)
  expect(to).toBeGreaterThan(from)
  return transpile(editor.slice(from, to))
}
const source: BackupSelectableSource = {
  id: 'agent:101', kind: 'agent', ref_id: 101, type: 'host', name: 'Original source',
  hostname: 'source101', node_name: 'source101', node_ip: '192.0.2.101',
  status: 'active', availability: 'online',
}
const scopes: ReturnType<typeof effectScope>[] = []
afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()); vi.clearAllMocks() })

async function editorReferences(discovery: boolean, targetId = 101, online = true) {
  const scope = effectScope()
  scopes.push(scope)
  const catalog = scope.run(() => useRestoreTargetCatalog())!
  vi.mocked(listBackupSelectableSources).mockImplementation(async params => {
    if (params?.scope === 'backup_configurations') return { count: 1, results: [source] }
    if (params?.ids) {
      const results = params.ids.split(',').map(id => ({
        ...source, id, ref_id: Number(id.split(':')[1]),
        availability: online ? 'online' as const : 'offline' as const,
      }))
      return { count: results.length, results }
    }
    return {
      count: 101,
      results: Array.from({ length: 100 }, (_, i) => ({ ...source, id: `agent:${i + 1}`, ref_id: i + 1 })),
    }
  })
  if (discovery) await catalog.reset()
  const configs = ref([{
    id: 7, source_type: 'agent', source_ref_id: 101,
    recovery_plans: [{ id: 11, target_type: 'agent', target_ref_id: targetId }],
  }])
  const realSources = ref(new Map<string, BackupSelectableSource>())
  const loadSources = new Function(
    'normalizeSourceIdList', 'isBackupSelectableSourceId', 'editConfigs', 'sourceIdFromConfig',
    'listBackupSelectableSources', 'restoreTargetCatalog', 'realSourceById', 'mapSelectableSource',
    'canViewProtectionSources', 'hasTenantPermission',
    body('async function loadWizardSources(', 'watch(restoreTargetCatalog.allRecords') + '\nreturn loadWizardSources;',
  )(
    normalizeSourceIdList, isBackupSelectableId, configs,
    (config: { source_type: string; source_ref_id: number }) => `${config.source_type}:${config.source_ref_id}`,
    listBackupSelectableSources, catalog, realSources, (value: unknown) => value,
    canViewProtectionSources, () => discovery,
  )
  await loadSources([source.id, `agent:${targetId}`])
  const missingFields = new Function('editConfigs', 'restoreTargetCatalog', 'restorePlanTargetReferenceIsValid',
    body('function recoveryDirPlanMissingFields(', 'function recoveryPlanMissingFields(') + '\nreturn recoveryDirPlanMissingFields;',
  )(configs, catalog, restorePlanTargetReferenceIsValid)
  const group = { sourceId: source.id, backupConfigId: 7 }
  const draft = {
    id: 'restore-plan-11', targetHostId: `agent:${targetId}`,
    sourcePath: '/data', restoreDir: '/restore', sourcePathValidation: 'valid', restoreDirValidation: 'valid',
  }
  const options = new Function(
    'restoreTargetCatalog', 'mapSelectableSource', 'createRecoveryTargetOptionFromSource',
    'savedRestoreTargetReference', 'editConfigs', 'realSourceById', 'createRecoveryTargetSummary',
    body('function createRecoveryTargetOptionsForGroup(', 'function onCreateRecoveryTargetVisible(') + '\nreturn createRecoveryTargetOptionsForGroup;',
  )(
    catalog, (value: unknown) => value,
    (row: BackupSelectableSource) => ({ value: row.id, selectable: catalog.isSelectable(row.id) }),
    savedRestoreTargetReference, configs, realSources,
    (row: BackupSelectableSource | undefined, fallback: { sourceName: string }) => ({ displayName: row?.name || fallback.sourceName }),
  )
  return { catalog, configs, realSources, missingFields, group, draft, options }
}

describe('Backup editor saved targets versus new target discovery', () => {
  it.each([false, true])('retains the saved original-source target with Source discovery=%s', async discovery => {
    const editor = await editorReferences(discovery)
    expect(editor.realSources.value.has(source.id)).toBe(true)
    expect(editor.missingFields(editor.group, editor.draft)).toEqual([])
    if (discovery) {
      expect(editor.catalog.getRecord(source.id)).toBeDefined()
      expect(listBackupSelectableSources).toHaveBeenCalledWith(expect.objectContaining({ ids: source.id }), expect.anything())
    } else {
      expect(editor.catalog.getRecord(source.id)).toBeUndefined()
      expect(editor.catalog.options()).toEqual([])
      expect(editor.options(editor.group, editor.draft)).toContainEqual(expect.objectContaining({
        value: source.id, selectable: false,
      }))
      expect(vi.mocked(listBackupSelectableSources).mock.calls.every(([params]) => params?.scope === 'backup_configurations')).toBe(true)
    }
  })

  it('retains another saved target without promoting it into inventory or new mappings', async () => {
    const editor = await editorReferences(false, 202)
    expect(editor.missingFields(editor.group, editor.draft)).toEqual([])
    expect(editor.catalog.getRecord('agent:202')).toBeUndefined()
    expect(editor.options(editor.group, editor.draft)).toContainEqual(expect.objectContaining({ value: 'agent:202', selectable: false }))
    expect(editor.missingFields(editor.group, { ...editor.draft, id: 'new-mapping' })).toEqual(['targetHostId'])
    expect(editor.missingFields({ ...editor.group, backupConfigId: 8 }, editor.draft)).toEqual(['targetHostId'])
    expect(editor.missingFields(editor.group, { ...editor.draft, targetHostId: source.id })).toEqual(['targetHostId'])
    expect(editor.catalog.options()).toEqual([])
  })

  it('hydrates an authorized saved target beyond the first page without unbounded discovery', async () => {
    const editor = await editorReferences(true, 202)
    expect(editor.missingFields(editor.group, editor.draft)).toEqual([])
    expect(editor.catalog.getRecord('agent:202')).toBeDefined()
    expect(editor.catalog.rows.value).toHaveLength(100)
    expect(editor.catalog.count.value).toBe(101)
    expect(vi.mocked(listBackupSelectableSources).mock.calls.filter(([params]) => !params?.ids)).toHaveLength(1)
  })

  it('does not override known offline target readiness with a retained-reference fallback', async () => {
    const editor = await editorReferences(true, 202, false)
    expect(editor.missingFields(editor.group, editor.draft)).toEqual(['targetHostId'])
  })

  it('does not advertise arbitrary configuration contexts as new targets', async () => {
    const editor = await editorReferences(false)
    const draft = { ...editor.draft, targetHostId: 'agent:303' }
    expect(editor.missingFields(editor.group, draft)).toEqual(['targetHostId'])
    expect(editor.options(editor.group, draft)).toEqual([])
    expect(editor.options({ ...editor.group, backupConfigId: 8 }, editor.draft)).toEqual([])
  })

  it('guards dropdown opening, search and pagination when Source viewing is absent', async () => {
    const editor = await editorReferences(false)
    const reset = vi.spyOn(editor.catalog, 'reset')
    const search = vi.spyOn(editor.catalog, 'setSearch')
    const more = vi.spyOn(editor.catalog, 'loadMore')
    const interactions = new Function(
      'canViewProtectionSources', 'hasTenantPermission', 'restoreTargetCatalog', 'recoveryTargetLoading', 'Event',
      body('function onCreateRecoveryTargetVisible(', 'function selectCreateRecoverySourceTarget(')
        + '\nreturn { onCreateRecoveryTargetVisible, onCreateRecoveryTargetPopupScroll, searchCreateRecoveryTargets };',
    )(canViewProtectionSources, () => false, editor.catalog, ref(false), Event)
    interactions.onCreateRecoveryTargetVisible(true)
    interactions.onCreateRecoveryTargetPopupScroll({ scrollHeight: 0, scrollTop: 0, clientHeight: 0 })
    interactions.searchCreateRecoveryTargets('unavailable source')
    expect(reset).not.toHaveBeenCalled()
    expect(search).not.toHaveBeenCalled()
    expect(more).not.toHaveBeenCalled()
  })
})
