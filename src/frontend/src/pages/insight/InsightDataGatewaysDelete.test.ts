import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { ref } from 'vue'
import ts from 'typescript'
import { describe, expect, it, vi } from 'vitest'
import { sanitizeErrorDetails } from '../../lib/errors/details'

const source = readFileSync(resolve(process.cwd(), 'src/pages/insight/InsightDataGateways.vue'), 'utf8')
const body = source.slice(source.indexOf('async function confirmDelete()'), source.indexOf('function onPaginationSizeChange()'))
const script = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

function setup(force = false) {
  const pendingDelete = ref([{ id: 1, name: 'offline' }, { id: 2, name: 'bound' }])
  const deleteLoading = ref(false)
  const deleteForce = ref(force)
  const deleteOpen = ref(true)
  const deleteStartErrors = ref<unknown[]>([])
  const lifecycleOps = { runBatch: vi.fn(), lastStartErrors: ref<Record<string, unknown>[]>([]) }
  const clearSelection = vi.fn()
  const confirm = new Function('pendingDelete', 'deleteLoading', 'deleteForce', 'deleteOpen', 'deleteStartErrors', 'lifecycleOps', 'clearSelection', 'sanitizeErrorDetails', 'apiErrorMessage', 't', `${script}; return confirmDelete`)(
    pendingDelete, deleteLoading, deleteForce, deleteOpen, deleteStartErrors, lifecycleOps, clearSelection,
    sanitizeErrorDetails, (error: Error) => error.message, (key: string) => key,
  ) as () => Promise<void>
  return { confirm, pendingDelete, deleteLoading, deleteForce, deleteOpen, deleteStartErrors, lifecycleOps }
}

describe('Gateway deletion mode recovery', () => {
  it.each(['node_offline', 'agent_uninstall_failed', 'completion_callback_timeout'])(
    'offers guidance without changing mode after %s',
    async (code) => {
      const state = setup()
      state.lifecycleOps.lastStartErrors.value = [{ node_id: 1, code }]
      state.lifecycleOps.runBatch.mockResolvedValue(false)
      await state.confirm()
      expect(state.deleteForce.value).toBe(false)
      expect(state.deleteStartErrors.value).toMatchObject([{ nodeId: 1, suggestForce: true }])
    },
  )

  it('does not auto-enable force and suggests it only for eligible error codes', async () => {
    const state = setup()
    state.lifecycleOps.lastStartErrors.value = [
      { node_id: 1, code: 'node_offline', error: 'offline' },
      { node_id: 2, code: 'bindings_exist', error: 'bound' },
    ]
    state.lifecycleOps.runBatch.mockResolvedValue(false)
    await state.confirm()
    expect(state.deleteForce.value).toBe(false)
    expect(state.deleteOpen.value).toBe(true)
    expect(state.deleteStartErrors.value).toMatchObject([{ nodeId: 1, suggestForce: true }, { nodeId: 2, suggestForce: false }])
    expect(state.lifecycleOps.runBatch).toHaveBeenCalledWith('remove', expect.any(Array), { skipConfirm: true, force: false })
    expect(state.deleteLoading.value).toBe(false)
  })

  it('preserves explicit force selection and excludes successfully started targets on retry', async () => {
    const state = setup(true)
    state.lifecycleOps.lastStartErrors.value = [{ node_id: 2, code: 'completion_callback_timeout' }]
    state.lifecycleOps.runBatch.mockResolvedValue(false)
    await state.confirm()
    expect(state.deleteForce.value).toBe(true)
    expect(state.pendingDelete.value.map(row => row.id)).toEqual([2])
    expect(state.deleteStartErrors.value).toMatchObject([{ suggestForce: false }])
  })

  it('snapshots request mode and blocks duplicate submissions while loading', async () => {
    const state = setup()
    let finish!: (value: boolean) => void
    state.lifecycleOps.runBatch.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const request = state.confirm()
    expect(state.deleteLoading.value).toBe(true)
    await state.confirm()
    expect(state.lifecycleOps.runBatch).toHaveBeenCalledTimes(1)
    finish(true)
    await request
    expect(state.deleteOpen.value).toBe(false)
    expect(state.deleteLoading.value).toBe(false)
  })
})
