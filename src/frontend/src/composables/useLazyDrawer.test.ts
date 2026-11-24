// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { ElDrawer } from 'element-plus'
import { defineComponent, effectScope, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { i18n } from '../i18n'
import { LazyDrawerFrame } from '../components/feedback/LazyDrawerFrame'
import { defineLazyDrawer, useDrawerDetailRequest, useDrawerMounted } from './useLazyDrawer'

const global = { plugins: [i18n], stubs: { teleport: true } }

function source(path: string) {
  return readFileSync(resolve(process.cwd(), 'src/pages', `${path}.vue`), 'utf8')
}

describe('on-demand business detail drawers', () => {
  it.each([
    ['node/Nodes', 'ProxyNodeDetailDrawer', 'proxyDetailMounted'],
    ['node/Repositories', 'TaskDetailDrawer', 'repositoryTaskDetailMounted'],
    ['ops/Tasks', 'TaskDetailDrawer', 'detailMounted'],
    ['protection/DataProtection', 'TaskDetailDrawer', 'backupTaskDetailMounted'],
    ['protection/DataProtection', 'FlowBackupSourceDetailDrawer', 'flowSourceDetailMounted'],
    ['protection/components/FlowBackupSourceDetailDrawer', 'TaskDetailDrawer', 'taskDetailMounted'],
    ['protection/BackupSources', 'HostSourceDetailDrawer', 'hostDetailMounted'],
    ['protection/BackupCreateWizard', 'HostSourceDetailDrawer', 'createHostDetailMounted'],
    ['protection/components/BackupSourceDetailLauncher', 'HostSourceDetailDrawer', 'hostMounted'],
    ['insight/InsightDataGateways', 'InsightGatewayDetailDrawer', 'detailMounted'],
    ['insight/InsightUsage', 'InsightUsageRequestDrawer', 'drawerMounted'],
    ['insight/InsightAiSettings', 'InsightAiModelDetailDrawer', 'detailMounted'],
    ['insight/InsightKnowledgeBase', 'InsightKnowledgeSourceDetailDrawer', 'detailMounted'],
    ['insight/InsightAssistants', 'InsightAssistantDetailDrawer', 'detailMounted'],
    ['insight/InsightSkills', 'InsightSkillDetailDrawer', 'detailMounted'],
    ['insight/InsightMcpServers', 'InsightMcpServerDetailDrawer', 'detailMounted'],
  ])('defers %s → %s', (path, component, mounted) => {
    const page = source(path)
    expect(page).toMatch(new RegExp(`const ${component} = defineLazyDrawer\\(\\(\\) => import\\('[^']*/${component}\\.vue'\\)[),]`))
    if (component === 'TaskDetailDrawer') {
      expect(page).toContain("titleKey: 'ops.task.detailTitle', loadingLayout: 'task'")
    }
    expect(page).not.toMatch(new RegExp(`import ${component} from`))
    expect(page).toMatch(new RegExp(`<${component}\\s+v-if="${mounted}"`))
  })

  it('does not preload the protection drawer when entering or changing steps', () => {
    const page = source('protection/DataProtection')
    expect(page).not.toMatch(/preload\w*SourceDetailDrawer/i)
    expect(page).toContain('scheduleInteractivePreload(preloadBackupCreateWizard)')
  })

  it('loads on first open and keeps the drawer mounted through close and reopen', async () => {
    const onOpened = vi.fn()
    const drawer = defineComponent({
      props: { modelValue: Boolean },
      emits: ['update:modelValue', 'saved'],
      setup: (props, { emit }) => () => h(LazyDrawerFrame, { onOpened }, () => h('button', {
        onClick: () => {
          emit('saved')
          emit('update:modelValue', false)
        },
      }, props.modelValue ? 'Open' : 'Closing')),
    })
    let finish!: (value: { default: typeof drawer }) => void
    const load = vi.fn(() => new Promise<{ default: typeof drawer }>(resolve => { finish = resolve }))
    const AsyncDrawer = defineLazyDrawer(load, { titleKey: 'ops.task.detailTitle', loadingLayout: 'task' })
    const open = ref(false)
    const saved = vi.fn()
    const wrapper = mount(defineComponent({
      setup() {
        const mounted = useDrawerMounted(() => open.value)
        return () => mounted.value ? h(AsyncDrawer, {
          modelValue: open.value,
          'onUpdate:modelValue': (value: boolean) => { open.value = value },
          onSaved: saved,
        }) : null
      },
    }), { global: { plugins: [i18n] }, attachTo: document.body })

    try {
      await flushPromises()
      expect(load).not.toHaveBeenCalled()
      expect(wrapper.find('[role="status"]').exists()).toBe(false)
      open.value = true
      await nextTick()
      const status = wrapper.get('[role="status"]')
      expect(status.get('.sr-only').text()).toBe(i18n.global.t('common.loading'))
      expect(status.find('.lazy-drawer-loading__status').exists()).toBe(false)
      expect(wrapper.get('[role="heading"]').text()).toBe(i18n.global.t('ops.task.detailTitle'))
      expect(wrapper.get('.lazy-drawer-loading__task-summary').isVisible()).toBe(true)
      const shell = wrapper.get('[role="dialog"]').element
      const drawerInstance = wrapper.getComponent(ElDrawer).vm
      drawerInstance.$emit('opened')

      finish({ default: drawer })
      await flushPromises()
      expect(wrapper.getComponent(ElDrawer).vm).toBe(drawerInstance)
      expect(wrapper.get('[role="dialog"]').element).toBe(shell)
      expect(wrapper.findAll('[role="dialog"]')).toHaveLength(1)
      expect(onOpened).toHaveBeenCalledOnce()
      expect(wrapper.get('.el-drawer__body button').text()).toBe('Open')
      expect(wrapper.find('[role="status"]').exists()).toBe(false)
      await wrapper.get('.el-drawer__body button').trigger('click')
      expect(saved).toHaveBeenCalledOnce()
      expect(open.value).toBe(false)
      open.value = true
      await nextTick()
      expect(wrapper.get('.el-drawer__body button').text()).toBe('Open')
      expect(load).toHaveBeenCalledOnce()
    } finally {
      wrapper.unmount()
    }
  })

  it('can close a loading drawer without waiting for its module', async () => {
    const drawer = defineLazyDrawer(() => new Promise<never>(() => {}))
    const open = ref(true)
    const wrapper = mount(defineComponent({
      setup: () => () => h(drawer, {
        open: open.value,
        'onUpdate:open': (visible: boolean) => { open.value = visible },
      }),
    }), { global })
    try {
      await nextTick()
      expect(wrapper.get('[role="status"]').exists()).toBe(true)
      wrapper.getComponent(ElDrawer).vm.$emit('update:modelValue', false)
      await nextTick()
      expect(open.value).toBe(false)
      expect(wrapper.getComponent(ElDrawer).props('modelValue')).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('replaces loading feedback with a recovery message if the module fails', async () => {
    const detail = defineComponent({ props: { modelValue: Boolean }, setup: () => () => h(LazyDrawerFrame, null, () => 'Details ready') })
    const load = vi.fn().mockRejectedValueOnce(new Error('module unavailable')).mockResolvedValueOnce({ default: detail })
    const drawer = defineLazyDrawer<typeof detail>(load)
    const wrapper = mount(drawer, { props: { modelValue: true }, global })
    try {
      await nextTick()
      expect(wrapper.get('[role="status"]').exists()).toBe(true)
      await flushPromises()
      expect(wrapper.get('[role="alert"]').exists()).toBe(true)
      await wrapper.get('[role="alert"] button').trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('Details ready')
      expect(load).toHaveBeenCalledTimes(2)
    } finally {
      wrapper.unmount()
    }
  })

  it('opens immediately while fetching details and ignores a response after close', async () => {
    const scope = effectScope()
    const open = ref(false)
    const { detailLoading, loadDetails } = scope.run(() => useDrawerDetailRequest(open))!
    let finish!: () => void
    let signal!: AbortSignal
    const apply = vi.fn()
    try {
      const pending = loadDetails(activeSignal => new Promise<void>(resolve => { signal = activeSignal; finish = resolve }), apply)
      expect(open.value).toBe(true)
      expect(detailLoading.value).toBe(true)
      open.value = false
      expect(signal.aborted).toBe(true)
      finish()
      await pending
      expect(apply).not.toHaveBeenCalled()
      expect(detailLoading.value).toBe(false)
    } finally {
      scope.stop()
    }
  })
})
