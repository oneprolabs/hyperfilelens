// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, reactive, ref } from 'vue'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { transpile } from 'typescript'
import { en } from '../../locales/en'
import { normalizeS3EndpointInput } from '../../lib/s3PlatformDisplay'
import TargetRepositoryPicker, { type TargetRepositoryItem } from './components/TargetRepositoryPicker.vue'
import { backupTargetIncompatibilityReason, backupTargetIncompatibilityReasonForSources } from './lib/backupTargetCompatibility'

const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')
function section(start: string, end: string) {
  const from = source.indexOf(start)
  const to = source.indexOf(end, from)
  if (from < 0 || to < 0) throw new Error(`Missing target assignment section: ${start}`)
  return source.slice(from, to)
}
// Exercise the wizard's actual handlers, computed guards and dialog markup with
// its real Picker/Endpoint controls, without mounting unrelated backup workflows.
const logic = [
  section('function getRealTarget(', 'function endpointForTarget('),
  section('function targetHealth(', 'function applyAddTargetS3RegionPreset('),
  section('const selectableTargets =', 'const addTargetTypeOptions ='),
  section('function targetIncompatibilityReasonForGroup(', 'const filteredRecoveryPlanGroups ='),
  section('const batchSelectedTarget =', 'function isTargetGroupMissing('),
  section('function applyBatchTarget()', 'function groupHasNasTarget('),
].join('\n')
const dialogStart = source.lastIndexOf('<el-dialog', source.indexOf('class="target-batch-dialog'))
const dialog = source.slice(dialogStart, source.indexOf('</el-dialog>', dialogStart) + '</el-dialog>'.length)

type Target = TargetRepositoryItem & { s3ExternalEndpoint?: string; s3InternalEndpoint?: string; crossProxyReady?: boolean }
type Group = { key: string; sourceId: string; sourceType: 'host' | 'nas'; platform: string }
const groups: Group[] = [
  { key: 'a', sourceId: 'agent:1', sourceType: 'host', platform: 'linux' },
  { key: 'b', sourceId: 'nas:2', sourceType: 'nas', platform: 'linux' },
  { key: 'c', sourceId: 'agent:3', sourceType: 'host', platform: 'linux' },
]
const repositories: Target[] = [
  { id: 'legacy', name: 'Existing target', repoType: 'S3', location: 's3://existing', health: 'online' },
  { id: 'dual', name: 'Dual endpoint target', repoType: 'S3', location: 's3://dual', health: 'online',
    s3ExternalEndpoint: 'https://external.example', s3InternalEndpoint: 'https://internal.example' },
  { id: 'disk', name: 'Different Proxy disk', repoType: 'PROXY_FS', location: '/backup', health: 'online',
    bindNodeType: 'proxy', bindNodeId: 11, crossProxyReady: false },
]
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()))

function render() {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  const state = {
    computed,
    normalizeS3EndpointInput,
    backupTargetIncompatibilityReason,
    backupTargetIncompatibilityReasonForSources,
    t: i18n.global.t,
    ElMessage: { warning: vi.fn(), success: vi.fn() },
    realTargets: ref<Target[]>(repositories.map((target) => ({ ...target }))),
    wizardSourceGroups: ref(groups.map((group) => ({ ...group }))),
    realSourceById: ref(new Map(groups.map((group) => [group.sourceId, { platform: group.platform, boundNodeId: 22 }]))),
    targetAssignmentCheckedGroupKeys: ref(['a', 'b']),
    targetAssignDialogMode: ref<'single' | 'batch'>('batch'),
    targetAssignActiveGroupKey: ref(''),
    targetAssignDialogOpen: ref(false),
    batchTargetPicker: reactive({ targetId: '', repoType: '' }),
    batchNasTargetMode: ref('per_directory_repo'),
    batchRepositoryEndpointType: ref(''),
    sourceTargetMap: ref<Record<string, string>>({ a: 'legacy', b: 'legacy', c: 'legacy' }),
    sourceEndpointTypeMap: ref<Record<string, string>>({ a: 'external', b: 'external', c: 'external' }),
    nasTargetModes: reactive<Record<string, string>>({}),
    targetsRefreshing: ref(false),
    refreshWizardTargets: vi.fn(async () => {}),
  }
  type Actions = {
    openBatchTargetDialog: () => Promise<void>
    openSingleTargetDialog: (group: Group) => Promise<void>
    targetAssignmentValid: { value: boolean }
  }
  const actions = new Function('state', `with (state) {
    ${transpile(logic)}
    return { batchSelectedTarget, batchSelectedTargetRequiresEndpoint,
      batchSelectedTargetIsNas, activeTargetAssignGroup, targetOptionsForBatch,
      targetAssignmentValid, targetAssignDialogTitle, checkedTargetGroups,
      onTargetPickerTargetChange, targetExternalEndpoint, targetInternalEndpoint,
      openBatchTargetDialog, openSingleTargetDialog, applyTargetAssignment };
  }`)(state) as Actions
  const wrapper = mount(defineComponent({
    components: { TargetRepositoryPicker },
    setup: () => ({ ...state, ...actions }),
    template: dialog,
  }), {
    global: {
      plugins: [ElementPlus, i18n],
      stubs: { ElDialog: {
        props: ['modelValue'],
        template: '<section v-if="modelValue"><slot /><footer><slot name="footer" /></footer></section>',
      } },
    },
  })
  wrappers.push(wrapper)
  const confirm = () => wrapper.get('footer .el-button--primary')
  const cancel = () => wrapper.get('footer .el-button:not(.el-button--primary)')
  return { wrapper, state, actions, confirm, cancel }
}

describe('backup target assignment dialog integration', () => {
  it('blocks a batch-incompatible target and applies a valid target only to the checked sources after Endpoint selection', async () => {
    const { wrapper, state, actions, confirm } = render()
    await actions.openBatchTargetDialog()
    await flushPromises()
    expect((wrapper.get('input[value="disk"]').element as HTMLInputElement).disabled).toBe(true)
    expect(confirm().attributes('disabled')).toBeDefined()
    await wrapper.get('input[value="dual"]').setValue(true)
    expect(confirm().attributes('disabled')).toBeDefined()
    expect(state.sourceTargetMap.value.a).toBe('legacy')
    await wrapper.get('input[value="internal"]').setValue(true)
    expect(confirm().attributes('disabled')).toBeUndefined()
    await confirm().trigger('click')
    expect(state.sourceTargetMap.value).toEqual({ a: 'dual', b: 'dual', c: 'legacy' })
    expect(state.sourceEndpointTypeMap.value).toEqual({ a: 'internal', b: 'internal', c: 'external' })
    expect(state.targetAssignDialogOpen.value).toBe(false)
  })

  it('preserves Endpoint choice through search, type filters and returning from an incompatible repository', async () => {
    const { wrapper, state, actions, confirm } = render()
    await actions.openBatchTargetDialog()
    await flushPromises()
    await wrapper.get('input[value="dual"]').setValue(true)
    await wrapper.get('input[value="internal"]').setValue(true)
    await wrapper.get('input.el-input__inner').setValue('Existing target')
    expect(wrapper.find('input[value="dual"]').exists()).toBe(false)
    expect(state.batchRepositoryEndpointType.value).toBe('internal')
    await wrapper.get('.target-repository-picker__filter:nth-child(3)').trigger('click')
    expect(state.batchTargetPicker.targetId).toBe('dual')
    expect(state.batchRepositoryEndpointType.value).toBe('internal')
    await wrapper.get('.target-repository-picker__notice button').trigger('click')
    await wrapper.get('.target-repository-picker__disabled-reason button').trigger('click')
    expect(wrapper.find('.target-endpoint-choice').exists()).toBe(false)
    await wrapper.get('input[value="dual"]').trigger('click')
    expect(wrapper.find('.target-endpoint-choice').exists()).toBe(true)
    expect(state.batchRepositoryEndpointType.value).toBe('internal')
    expect(confirm().attributes('disabled')).toBeUndefined()
  })

  it('discards batch draft changes on cancel and resets the draft when reopened', async () => {
    const { wrapper, state, actions, cancel, confirm } = render()
    await actions.openBatchTargetDialog()
    await flushPromises()
    await wrapper.get('input[value="dual"]').setValue(true)
    await wrapper.get('input[value="external"]').setValue(true)
    await cancel().trigger('click')
    expect(state.sourceTargetMap.value).toEqual({ a: 'legacy', b: 'legacy', c: 'legacy' })
    expect(state.sourceEndpointTypeMap.value).toEqual({ a: 'external', b: 'external', c: 'external' })
    await actions.openBatchTargetDialog()
    await flushPromises()
    expect(state.batchTargetPicker.targetId).toBe('')
    expect(state.batchRepositoryEndpointType.value).toBe('')
    expect(confirm().attributes('disabled')).toBeDefined()
  })

  it('blocks a selection made invalid by refresh or a newly checked incompatible source', async () => {
    const { wrapper, state, actions, confirm } = render()
    state.targetAssignmentCheckedGroupKeys.value = ['a']
    await actions.openBatchTargetDialog()
    await flushPromises()
    await wrapper.get('input[value="disk"]').setValue(true)
    expect(confirm().attributes('disabled')).toBeUndefined()
    state.targetAssignmentCheckedGroupKeys.value = ['a', 'b']
    await flushPromises()
    expect(confirm().attributes('disabled')).toBeDefined()
    expect(wrapper.get('.target-repository-picker__notice--unavailable').text()).toContain('no longer available')
    await wrapper.get('input[value="dual"]').setValue(true)
    await wrapper.get('input[value="external"]').setValue(true)
    state.realTargets.value = state.realTargets.value.map((target) => target.id === 'dual'
      ? { ...target, health: 'offline' } : target)
    await flushPromises()
    expect(confirm().attributes('disabled')).toBeDefined()
    expect(wrapper.find('.target-endpoint-choice').exists()).toBe(false)
    expect(wrapper.get('.target-repository-picker__notice--unavailable').find('button').exists()).toBe(false)
    expect(state.sourceTargetMap.value.a).toBe('legacy')
  })

  it('restores a single-source draft on reopen and changes only that source on confirmation', async () => {
    const { wrapper, state, actions, cancel, confirm } = render()
    state.sourceTargetMap.value.a = 'dual'
    state.sourceEndpointTypeMap.value.a = 'internal'
    await actions.openSingleTargetDialog(groups[0]!)
    await flushPromises()
    expect(state.batchRepositoryEndpointType.value).toBe('internal')
    await wrapper.get('input[value="legacy"]').setValue(true)
    await cancel().trigger('click')
    expect(state.sourceTargetMap.value.a).toBe('dual')
    expect(state.sourceEndpointTypeMap.value.a).toBe('internal')
    await actions.openSingleTargetDialog(groups[0]!)
    await flushPromises()
    expect(state.batchTargetPicker.targetId).toBe('dual')
    expect(state.batchRepositoryEndpointType.value).toBe('internal')
    await wrapper.get('input[value="external"]').setValue(true)
    await confirm().trigger('click')
    expect(state.sourceTargetMap.value).toEqual({ a: 'dual', b: 'legacy', c: 'legacy' })
    expect(state.sourceEndpointTypeMap.value).toEqual({ a: 'external', b: 'external', c: 'external' })
  })
})
