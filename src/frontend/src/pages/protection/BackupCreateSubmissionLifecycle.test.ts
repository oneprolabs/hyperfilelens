import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { transpile } from 'typescript'

const source = readFileSync(new URL('./BackupCreateWizard.vue', import.meta.url), 'utf8')
const createSource = source.slice(
  source.indexOf('async function runCreateBackup()'),
  source.indexOf('function editableGroupPayloads()'),
)
const finishSource = source.slice(
  source.indexOf('function finishCreateAndGoToStep3('),
  source.indexOf('type BackupPathType'),
)

function createHarness() {
  const backup = (id: number) => ({
    key: `source-${id}`,
    name: `Test configuration ${id}`,
    source: { id: `agent:${id}`, type: 'host', pathItems: [] },
    targetId: '34',
  })
  const createPhase = { value: 'form' }
  const backups = [backup(12), backup(13)]
  const state = {
    createSubmissionActive: { get value() { return createPhase.value === 'waiting' } },
    createPhase,
    createOpen: { value: true },
    createWizardSteps: { value: [] },
    wizardSourceGroups: { value: backups.map((item) => ({ key: item.key })) },
    createItemStates: { value: {} as Record<string, string> },
    step1Selection: { value: ['agent:12', 'agent:13'] },
    step2Sources: { value: ['agent:12', 'agent:13'] },
    buildCreateBackupPayload: () => ({ backups }),
    parseSourceRefId: (id: string) => Number(id.split(':')[1]),
    createIdempotencyKey: () => 'stable-retry-key',
    createBackupConfig: vi.fn().mockResolvedValue({ id: 56, status: 'active' }),
    normalizeThrownError: () => ({ errorCode: 'VALIDATION.ERROR' }),
    showBackupConfigCreateError: vi.fn(),
    sourceHasBackupConfig: () => true,
    notifySuccess: vi.fn(),
    emit: vi.fn(),
    props: { embedded: true },
    logger: { error: vi.fn() },
    t: (key: string) => key,
  }
  const run = new Function('state', `
    with (state) {
      ${transpile(finishSource)}
      ${transpile(createSource)}
      return runCreateBackup;
    }
  `)(state) as () => Promise<void>
  return { state, run }
}

describe('backup configuration submission lifecycle', () => {
  it('keeps the confirmation open while submitting, then closes and notifies on success', async () => {
    const { state, run } = createHarness()
    let resolve!: (value: unknown) => void
    state.createBackupConfig.mockImplementationOnce(() => new Promise((done) => { resolve = done }))
    const submission = run()
    expect(state.createPhase.value).toBe('waiting')
    expect(state.createOpen.value).toBe(true)
    // A second click must not submit the batch again.
    await run()
    expect(state.createBackupConfig).toHaveBeenCalledTimes(1)
    resolve({ id: 55, status: 'active' })
    await submission
    expect(state.createPhase.value).toBe('form')
    expect(state.createOpen.value).toBe(false)
    expect(state.notifySuccess).toHaveBeenCalledOnce()
    expect(state.emit).toHaveBeenCalledWith('createCompleted', expect.objectContaining({
      items: expect.any(Array),
    }))
  })

  it('restores the button and keeps the form and selections on failure', async () => {
    const { state, run } = createHarness()
    state.createBackupConfig.mockRejectedValue(new Error('Creation failed'))
    await run()
    expect(state.createPhase.value).toBe('form')
    expect(state.createOpen.value).toBe(true)
    expect(state.step1Selection.value).toEqual(['agent:12', 'agent:13'])
    expect(state.showBackupConfigCreateError).toHaveBeenCalledTimes(2)
    expect(state.notifySuccess).not.toHaveBeenCalled()
    expect(state.emit).not.toHaveBeenCalled()
  })

  it('preserves partial successes without closing or resubmitting the successful source', async () => {
    const { state, run } = createHarness()
    state.createBackupConfig.mockRejectedValueOnce(new Error('Creation failed'))
    await run()
    expect(state.createOpen.value).toBe(true)
    expect(state.createPhase.value).toBe('form')
    expect(state.step1Selection.value).toEqual(['agent:12'])
    expect(state.emit).toHaveBeenCalledWith('createPartial', expect.any(Object))
    expect(state.notifySuccess).not.toHaveBeenCalled()
  })

  it('keeps unknown outcomes and stops submitting the remainder on timeout', async () => {
    const { state, run } = createHarness()
    state.normalizeThrownError = () => ({ errorCode: 'NETWORK.TIMEOUT' })
    state.createBackupConfig.mockRejectedValue(new Error('Request timed out'))
    await run()
    expect(state.createOpen.value).toBe(true)
    expect(state.createPhase.value).toBe('form')
    expect(state.createItemStates.value).toEqual({
      'agent:12': 'unknown',
      'agent:13': 'not_attempted',
    })
    expect(state.createBackupConfig).toHaveBeenCalledOnce()
    expect(state.emit).not.toHaveBeenCalled()
  })
})
