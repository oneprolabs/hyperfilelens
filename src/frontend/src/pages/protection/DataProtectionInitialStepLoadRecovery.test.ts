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

describe('backup wizard initial step load recovery', () => {
  it('does not reload Step 1 when remembered pipeline rows change', () => {
    expect(page).not.toContain('watch(step2PipelineSourceIds')
  })

  it('uses the initial Step 1 page count instead of requesting it twice', () => {
    expect(page).toContain('if (flowMainStep.value !== 0) await refreshBackupSourcePoolCount()')
  })

  it('never reuses an aborted Step 1 request', () => {
    const loader = sourceBetween('async function loadBackupSelectable', 'async function loadStep2Selectable')

    expect(page).toContain("new Map<string, { promise: Promise<void>; signal: AbortSignal }>()")
    expect(loader).toContain('if (running && !running.signal.aborted) return running.promise')
    expect(loader).toContain('if (running) backupSelectableRequests.delete(key)')
    expect(loader).toContain('if (current?.promise === request) backupSelectableRequests.delete(key)')
  })

  it('retries Step 1 after a non-abort loading failure', () => {
    const loader = sourceBetween('async function loadBackupSelectable', 'async function loadStep2Selectable')

    expect(loader).toContain('scheduleFlowStepLoadRetry(0)')
    expect(loader).toContain('clearFlowStepLoadRetry(0)')
  })

  it('retries Step 2 after a non-abort loading failure', () => {
    const refresh = sourceBetween('async function refreshFlowStepData', 'function flowRowFromSourceId')

    expect(refresh).toContain('if (step === 1) scheduleFlowStepLoadRetry(1)')
    expect(page).toContain('clearFlowStepLoadRetry(1)')
  })

  it('keeps automatic retries silent after reporting the initial failure', () => {
    expect(page).toContain("{ showLoading: false, showError: false }")
    expect(page).toContain("if (options.showError !== false) showApiError(e)")
  })

  it('limits automatic recovery to one retry per step entry', () => {
    expect(page).toContain('const flowStepLoadRetryAttempted: Record<0 | 1, boolean>')
    expect(page).toContain('|| flowStepLoadRetryAttempted[step]')
    expect(page).toContain('flowStepLoadRetryAttempted[step] = true')
  })

  it('stops retries after navigation and unmount', () => {
    expect(page).toContain('if (step !== 0) clearFlowStepLoadRetry(0)')
    expect(page).toContain('if (step !== 1) clearFlowStepLoadRetry(1)')
    expect(page).toContain('clearFlowStepLoadRetries()')
  })
})
