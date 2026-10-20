// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import ts from 'typescript'
import { describe, expect, it, vi } from 'vitest'
import HflHelpTip from '../../components/HflHelpTip.vue'

const wizard = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)
// Render the actual row template without bootstrapping the entire backup wizard.
const rowStart = wizard.lastIndexOf('<div', wizard.indexOf('class="create-dir-row create-dir-row--tree"'))
const rowTemplate = wizard.slice(rowStart, wizard.indexOf('</ElTooltip>', rowStart))
const reason = 'This directory is managed by the HyperFileLens Agent.'
const captureSource = wizard.slice(
  wizard.indexOf('function onSourceTreeClickCapture('),
  wizard.indexOf('\nfunction addPickedSourceFor('),
)

function captureHandler() {
  const warn = vi.fn(() => true)
  const compiled = ts.transpileModule(captureSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText
  const handler = new Function(
    'addedDirTreeDisableReason', 'warnSourceTreeConflict', 'basenamePath',
    `${compiled}; return onSourceTreeClickCapture;`,
  )(() => 'A parent path has been added.', warn, (path: string) => path) as
    (sourceId: string, event: MouseEvent) => void
  return { handler, warn }
}

function mountRow(protectedPath = true) {
  const onSourceTreeRowClick = vi.fn()
  const wrapper = mount(defineComponent({
    components: { HflHelpTip },
    setup: () => ({
      row: { id: 'source-1' },
      data: {
        label: 'agent',
        path: '/agent',
        path_type: 'directory',
        protectionReason: protectedPath ? 'agent_internal' : undefined,
      },
      Folder: 'span',
      FileIcon: 'span',
      sourceTreeProtectionReason: () => protectedPath ? reason : '',
      addedDirTreeDisableReason: () => '',
      isSourceDirectoryRefreshing: () => false,
      refreshCreateSourceDirectory: vi.fn(),
      t: (key: string) => key === 'protection.backupsPage.dirNotSelectable' ? '不可选' : key,
      onSourceTreeRowClick,
    }),
    template: rowTemplate,
  }), {
    global: {
      stubs: {
        ElTooltip: {
          template: '<slot />',
          props: ['content', 'disabled', 'placement', 'showAfter', 'popperClass', 'trigger'],
        },
        ShieldAlert: true,
        RefreshCw: true,
      },
    },
  })
  return { wrapper, onSourceTreeRowClick }
}

describe('BackupCreateWizard disabled directories', () => {
  it('uses only the help tooltip for a protected row, with a focusable reason button', async () => {
    const { wrapper, onSourceTreeRowClick } = mountRow()
    expect(wrapper.attributes('aria-disabled')).toBe('true')
    expect(wrapper.classes()).toContain('create-dir-row--disabled')
    expect(wrapper.findAll('[title]')).toHaveLength(0)
    const help = wrapper.getComponent(HflHelpTip)
    expect(help.props('content')).toBe(reason)
    const button = help.get('button')
    expect(button.text()).toBe('不可选')
    expect(button.find('svg').exists()).toBe(true)
    expect(button.classes()).toContain('hfl-help-tip--labeled')
    expect(wrapper.findAllComponents(HflHelpTip)).toHaveLength(1)
    expect(button.attributes('aria-label')).toBe(`不可选: ${reason}`)
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('aria-disabled')).toBe('false')
    expect(button.attributes('tabindex')).not.toBe('-1')

    await button.trigger('click')
    expect(onSourceTreeRowClick).not.toHaveBeenCalled()
    await wrapper.trigger('click')
    expect(onSourceTreeRowClick).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('exempts help text and SVG clicks from the actual parent capture handler', async () => {
    const { wrapper, onSourceTreeRowClick } = mountRow()
    const { handler, warn } = captureHandler()
    const tree = document.createElement('div')
    const node = document.createElement('div')
    node.className = 'el-tree-node__content'
    tree.append(node)
    node.append(wrapper.element)
    tree.addEventListener('click', (event) => handler('source-1', event), true)

    for (const selector of ['button', 'button span', 'button svg', 'button svg circle']) {
      await wrapper.get(selector).trigger('click')
      expect(warn).not.toHaveBeenCalled()
      expect(onSourceTreeRowClick).not.toHaveBeenCalled()
    }
    // A directory row click must still hit the conflict guard and stop propagation.
    await wrapper.get('.create-dir-row__label').trigger('click')
    expect(warn).toHaveBeenCalledOnce()
    expect(onSourceTreeRowClick).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('leaves ordinary directory interaction and refresh available', async () => {
    const { wrapper, onSourceTreeRowClick } = mountRow(false)
    expect(wrapper.attributes('aria-disabled')).toBe('false')
    expect(wrapper.classes()).not.toContain('create-dir-row--disabled')
    expect(wrapper.findComponent(HflHelpTip).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('不可选')
    expect(wrapper.find('.hfl-dir-tree-node__refresh').exists()).toBe(true)
    await wrapper.trigger('click')
    expect(onSourceTreeRowClick).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('keeps disabled rows neutral even when their tree node is hovered or current', () => {
    expect(wizard).toContain('.el-tree-node__content:hover) .create-dir-row--tree:not(.create-dir-row--disabled)')
    expect(wizard).toContain('.el-tree-node.is-checked > .el-tree-node__content) .create-dir-row--tree:not(.create-dir-row--disabled)')
    expect(wizard).toMatch(/\.el-tree-node > \.el-tree-node__content:has\(\.create-dir-row--disabled\)\) \{\s*background: transparent;\s*cursor: not-allowed;/)
    expect(wizard).toMatch(/\.create-dir-row--tree\.create-dir-row--disabled \{\s*border: 0;\s*background: var\(--el-fill-color-light\);/)
    expect(wizard).toMatch(/\.create-dir-row--disabled \.create-dir-row__label \{\s*color: var\(--el-text-color-secondary\);\s*font-weight: 400;/)
    expect(wizard).toContain('.create-dir-row__disabled-icon:focus-visible')
    expect(wizard).not.toContain('background: rgba(203, 213, 225, 0.7)')
    expect(wizard).not.toContain('background: rgba(203, 213, 225, 0.78)')
  })
})
