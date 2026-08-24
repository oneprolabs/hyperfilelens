import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

function sourceBetween(startMarker: string, endMarker: string) {
  const start = page.indexOf(startMarker)
  const end = page.indexOf(endMarker, start + 1)
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return page.slice(start, end)
}

describe('backup wizard reset → Backup Configuration list (#362)', () => {
  it('shows step=2 API rows on Backup Configuration without filtering by stale step-3 caches', () => {
    const pendingList = sourceBetween(
      'const step2PendingSourceList = computed(() => {',
      'const step3ConfiguredSourceIds = computed(() =>',
    )
    expect(pendingList).toContain('if (flowMainStep.value === 1) return step2SourceList.value')
    expect(pendingList).toContain('sourceHasBackupConfig(row.id)')
  })

  it('refreshes pipeline ids when loading Backup Configuration without overwriting filtered pager totals', () => {
    const refreshFlow = sourceBetween(
      'async function refreshFlowStepData(',
      'function flowRowFromSourceId(id: string)',
    )
    expect(refreshFlow).toContain('if (step === 1) {')
    expect(refreshFlow).toContain('await refreshPipelineStep2PlusIds(signal)')
    expect(refreshFlow).toContain('await loadStep2Selectable({ signal })')
    expect(refreshFlow).not.toContain('syncWizardCountsFromPipeline()')
    expect(refreshFlow).not.toContain('syncStep2WizardCountFromPipeline()')
  })

  it('tracks queued reset source ids across step3 pages and refreshes pipeline only when finished', () => {
    expect(page).toContain('const pendingResetPipelineSourceIds = ref<string[]>([])')
    expect(page).toContain('trackResetPipelineSources(sourceIdList)')

    const refreshStep3 = sourceBetween(
      'async function refreshStep3SourceList()',
      'function syncStep3AutoRefresh()',
    )
    expect(refreshStep3).toContain('const resetTrackedIds = collectResetTrackedIds()')
    expect(refreshStep3).toContain('const configsLoaded = await refreshBackupConfigs(signal)')
    expect(refreshStep3).toContain('if (!configsLoaded) return')
    expect(refreshStep3).toContain('selectFinishedResetSourceIds(')
    expect(refreshStep3).toContain('selectTerminalFailedResetSourceIds(')
    expect(refreshStep3).toContain('untrackResetPipelineSources(failedIds)')
    expect(refreshStep3).toContain('if (!finishedIds.length) return')
    expect(refreshStep3).toContain('await refreshPipelineStep2PlusIds(signal)')
    expect(refreshStep3).toContain('syncStep2WizardCountFromPipeline()')
    expect(refreshStep3).toContain('untrackResetPipelineSources(finishedIds)')
    expect(refreshStep3).not.toContain('syncWizardCountsFromPipeline()')
    expect(refreshStep3).not.toContain('step3Ids')
  })

  it('keeps Step 3 auto-refresh active while reset pipeline sources are tracked', () => {
    const refresh = sourceBetween(
      'function hasRunningStep3Tasks()',
      'function stopStep3AutoRefresh',
    )
    expect(refresh).toContain('if (pendingResetPipelineSourceIds.value.length > 0) return true')
  })

  it('returns false from refreshBackupConfigs soft-failure so reset completion is not inferred', () => {
    const refreshConfigs = sourceBetween(
      'async function refreshBackupConfigs(',
      'function displayNameForSource(type: string, refId: number, fallback: string)',
    )
    expect(refreshConfigs).toContain('return true')
    expect(refreshConfigs).toContain('return false')
  })
})
