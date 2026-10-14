// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { ElTable, ElTag } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import { describe, expect, it } from 'vitest'
import DangerConfirmDialog, { type DangerConfirmItem } from './DangerConfirmDialog.vue'

const dialogSource = readFileSync(
  resolve(process.cwd(), 'src/components/DangerConfirmDialog.vue'),
  'utf8',
)
const nodesPage = readFileSync(
  resolve(process.cwd(), 'src/pages/node/Nodes.vue'),
  'utf8',
)
const locale = readFileSync(
  resolve(process.cwd(), 'src/locales/en.ts'),
  'utf8',
)

const ElDialogStub = defineComponent({
  props: {
    modelValue: Boolean,
    width: String,
  },
  template: `
    <section data-test="dialog" :data-width="width">
      <slot name="header" />
      <slot />
      <slot name="footer" />
    </section>
  `,
})

const ElButtonStub = defineComponent({
  props: { disabled: Boolean },
  emits: ['click'],
  template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
})

function mountDialog(items?: DangerConfirmItem[], width?: string) {
  return mount(DangerConfirmDialog, {
    props: {
      modelValue: true,
      title: 'Confirm cleanup',
      ...(items === undefined ? {} : { items }),
      ...(width === undefined ? {} : { width }),
    },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      directives: { tableOverflowTitle: {} },
      stubs: {
        ElTable: defineComponent({ template: '<div data-test="element-resource-table" />' }),
        ElButton: ElButtonStub,
        ElDialog: ElDialogStub,
        ExactKeywordConfirmInput: defineComponent({ template: '<input>' }),
      },
    },
  })
}

function functionSource(source: string, start: string, end: string) {
  return source.slice(source.indexOf(start), source.indexOf(end))
}

describe('Danger confirmation item layout', () => {
  it('can hide the details column without changing the default three-column layout', async () => {
    const wrapper = mountDialog([{
      name: '123@123.com', description: 'Should not be duplicated',
      status: { label: 'Active', tone: 'success' },
    }])
    await wrapper.setProps({ showItemDetails: false, itemNameLabel: 'Member' })
    expect(wrapper.findAll('th').map((cell) => cell.text())).toEqual(['Member', 'Status'])
    expect(wrapper.findAll('col')).toHaveLength(2)
    expect(wrapper.find('tbody tr').findAll('td')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('Should not be duplicated')
    await wrapper.setProps({ showItemDetails: true })
    expect(wrapper.findAll('th').map((cell) => cell.text())).toEqual(['Member', 'Status', 'Details'])
    expect(wrapper.text()).toContain('Should not be duplicated')
    wrapper.unmount()
  })

  it('vertically centers the header icon, title block, and close button', () => {
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__header\s*{[^}]*align-items:\s*center;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__titles\s*{[^}]*justify-content:\s*center;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__close\s*{[^}]*align-self:\s*center;/s)
  })
  it('preserves built-in labels when optional copy is omitted', async () => {
    const wrapper = mountDialog([{ name: 'proxy-01' }])

    expect(wrapper.findAll('th').map(cell => cell.text())).toEqual([
      'Name',
      'Status',
      'Details',
    ])
    expect(wrapper.get('.hfl-danger-confirm__close').attributes('aria-label')).toBe('Close')
    expect(wrapper.get('.hfl-danger-confirm__footer').text()).toContain('Cancel')
    expect(wrapper.get('.hfl-danger-confirm__footer').text()).toContain('Confirm')

    await wrapper.setProps({ pending: true })
    expect(wrapper.get('.hfl-danger-confirm__pending').text()).toContain('Checking dependencies...')
    wrapper.unmount()
  })

  it('keeps item-dialog width stable while asynchronous items load', async () => {
    const wrapper = mountDialog([])
    const itemWidth = 'min(600px, calc(100vw - 32px))'

    expect(wrapper.get('[data-test="dialog"]').attributes('data-width')).toBe(itemWidth)

    await wrapper.setProps({
      items: [{
        name: 'proxy-01',
        description: 'Last seen 2026-08-11 11:05:58',
        status: { label: 'Offline', tone: 'danger' },
        warning: 'Upgrade the Agent before Strict Cleanup.',
      }],
    })

    expect(wrapper.get('[data-test="dialog"]').attributes('data-width')).toBe(itemWidth)
    expect(wrapper.get('.hfl-danger-confirm__status').text()).toBe('Offline')
    expect(wrapper.get('.hfl-danger-confirm__item-desc').text()).toContain('Last seen')
    expect(wrapper.get('.hfl-danger-confirm__item-warning').text()).toContain('Upgrade the Agent')
    wrapper.unmount()
  })

  it('retains compact and explicitly requested dialog widths', () => {
    const compact = mountDialog()
    const explicit = mountDialog([], '640px')

    expect(compact.get('[data-test="dialog"]').attributes('data-width'))
      .toBe('min(480px, calc(100vw - 32px))')
    expect(explicit.get('[data-test="dialog"]').attributes('data-width')).toBe('640px')
    compact.unmount()
    explicit.unmount()
  })

  it('lets status-free items use the full mobile row', () => {
    const wrapper = mountDialog([{
      name: 'A long item name that should not reserve an empty status column',
    }])

    expect(wrapper.get('.hfl-danger-confirm__item-name-cell').classes())
      .toContain('hfl-danger-confirm__item-name-cell--full')
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__item-name-cell--full\s*{[^}]*grid-column:\s*1 \/ -1;/s)
    wrapper.unmount()
  })

  it('keeps status badges inside their table column', () => {
    expect(dialogSource).not.toContain(':title="item.status.label"')
    expect(dialogSource).not.toContain(':title="item.name"')
    expect(dialogSource).not.toContain(':title="item.description || item.hint"')
    expect(dialogSource).toContain(':data-table-overflow-title="item.name"')
    expect(dialogSource).toContain(':data-table-overflow-title="item.description || item.hint"')
    expect(dialogSource).toContain('data-table-overflow-explicit-only')
    expect(dialogSource).toContain(':data-table-overflow-title="item.status.label"')
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status\s*{[^}]*max-width:\s*100%;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status-text\s*{[^}]*overflow:\s*hidden;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status-text\s*{[^}]*text-overflow:\s*ellipsis;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status-text\s*{[^}]*line-height:\s*18px;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status :deep\(\.el-tag__content\)\s*{[^}]*line-height:\s*18px;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__item-warning\s*{[^}]*grid-template-columns:\s*12px minmax\(0, 1fr\);/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__item-warning > span\s*{[^}]*min-width:\s*0;/s)
  })

  it('stacks item details without horizontal scrolling on narrow screens', () => {
    expect(dialogSource).toMatch(/@media \(max-width: 560px\)[\s\S]*?overflow-x:\s*hidden;/)
    expect(dialogSource).toMatch(/@media \(max-width: 560px\)[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) minmax\(88px, 42%\);/)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__item-details-cell\s*{[^}]*grid-column:\s*1 \/ -1;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__item-name,[\s\S]*?white-space:\s*normal;/)
  })

  it('separates node state, last-seen metadata, and cleanup warnings', () => {
    const itemMapping = functionSource(
      nodesPage,
      'function nodeDeleteDialogItem',
      'async function deleteSelectedNodes',
    )

    expect(itemMapping).toContain("t('nodesPage.statusOffline')")
    expect(itemMapping).toContain("t('nodesPage.lastSeenDetail'")
    expect(itemMapping).toContain('warning: pendingDeleteUpgradeRequired.value.has(row.id)')
    expect(itemMapping).not.toContain('statusOfflineWithLastSeen')
    expect(dialogSource).toContain('warning?: string')
    expect(locale).toContain("lastSeenDetail: 'Last seen {time}'")
  })
})

describe('Danger confirmation context reset', () => {
  it('clears confirmation only when the explicit target context or mode changes', async () => {
    const wrapper = mount(DangerConfirmDialog, {
      props: { modelValue: true, title: 'Delete', confirmMode: 'keyword', confirmationContext: '1,2' },
      global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        directives: { tableOverflowTitle: {} },
        stubs: {
          ElButton: ElButtonStub,
          ElDialog: ElDialogStub,
          ExactKeywordConfirmInput: defineComponent({
            props: { modelValue: String },
            emits: ['update:modelValue'],
            template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)">',
          }),
        },
      },
    })
    const input = wrapper.get('input')
    await input.setValue('DELETE')
    await wrapper.setProps({ items: [{ name: 'offline' }] })
    expect((input.element as HTMLInputElement).value).toBe('DELETE')
    await wrapper.setProps({ confirmationContext: '2' })
    expect((input.element as HTMLInputElement).value).toBe('')
    await input.setValue('DELETE')
    await wrapper.setProps({ confirmKeyword: 'FORCE DELETE' })
    expect((input.element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })
})

describe('Shared dialog resource table styling', () => {
  it('renders the real small Element Plus table and light small status tags', async () => {
    const wrapper = mount(DangerConfirmDialog, {
      props: {
        modelValue: true,
        title: 'Delete proxy',
        items: [{ name: 'proxy-01', status: { label: 'Offline', tone: 'danger' } }],
      },
      global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        directives: { tableOverflowTitle: {} },
        stubs: { ElDialog: ElDialogStub, ElButton: ElButtonStub },
      },
    })
    const table = wrapper.findComponent(ElTable)
    expect(table.props('size')).toBe('small')
    expect(table.props('maxHeight')).toBe('260')
    expect(table.classes()).toContain('hfl-flow-action-dialog__table')
    expect(table.find('.el-table__header').exists()).toBe(true)
    expect(table.find('.el-table__body').exists()).toBe(true)
    const tag = wrapper.findComponent(ElTag)
    expect(tag.props('size')).toBe('small')
    expect(tag.props('effect')).toBe('light')
    expect(tag.props('type')).toBe('danger')
    wrapper.unmount()
  })

  it('uses the source deregistration table presentation without generic uppercase table styling', () => {
    const wrapper = mountDialog([{ name: 'proxy-01', status: { label: 'Online', tone: 'success' } }])
    expect(wrapper.get('table').classes()).toContain('hfl-dialog-resource-table')
    expect(wrapper.get('table').classes()).not.toContain('hyper-table')
    const sourceBody = readFileSync(resolve(process.cwd(), 'src/components/BackupSourceUnregisterDialogBody.vue'), 'utf8')
    expect(sourceBody).toContain('hfl-dialog-resource-table')
    const shared = readFileSync(resolve(process.cwd(), 'src/styles/dialog-resource-table.css'), 'utf8')
    expect(shared).toContain('padding: 8px 12px')
    expect(shared).toContain('padding: 10px 12px')
    expect(shared).toContain('font-size: 13px')
    expect(shared).not.toContain('uppercase')
    expect(dialogSource).toContain('<ElTable')
    expect(dialogSource).toContain('size="small"')
    expect(dialogSource).toContain('max-height="260"')
    expect(dialogSource).toContain('<ElTag')
    expect(dialogSource).toContain('effect="light"')
    expect(dialogSource).not.toContain('hfl-danger-confirm__status--danger')
    expect(wrapper.find('[data-test="element-resource-table"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('Localized danger dialog column headers', () => {
  it('translates default headers and preserves explicitly supplied business labels', async () => {
    const zh = JSON.parse(readFileSync(resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'), 'utf8'))
    const wrapper = mount(DangerConfirmDialog, {
      props: { modelValue: true, title: '删除', items: [{ name: 'proxy' }] },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'zh-hans', messages: { en, 'zh-hans': zh } })],
        directives: { tableOverflowTitle: {} },
        stubs: { ElDialog: ElDialogStub, ElButton: ElButtonStub },
      },
    })
    await flushPromises()
    const table = wrapper.findComponent(ElTable)
    expect(table.findAll('.el-table__header th .cell').map(cell => cell.text())).toEqual(['名称', '状态', '详情'])
    expect(wrapper.findAll('.hfl-danger-confirm__mobile-items th').map(cell => cell.text())).toEqual(['名称', '状态', '详情'])
    await wrapper.setProps({ itemNameLabel: '备份源', itemDetailsLabel: '存储路径' })
    await flushPromises()
    expect(table.findAll('.el-table__header th .cell').map(cell => cell.text())).toEqual(['备份源', '状态', '存储路径'])
    wrapper.unmount()
  })
})

describe('Chat deletion keyword enforcement', () => {
  it.each(['DELETE', 'FORCE DELETE'])('requires exact %s and clears input after close/reopen', async (keyword) => {
    const wrapper = mount(DangerConfirmDialog, {
      props: { modelValue: true, title: 'Delete Chat', confirmMode: 'keyword', confirmKeyword: keyword, confirmationContext: '444:normal' },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        directives: { tableOverflowTitle: {} },
        stubs: {
          ElDialog: ElDialogStub,
          ElButton: ElButtonStub,
          ExactKeywordConfirmInput: defineComponent({
            props: { modelValue: String },
            emits: ['update:modelValue', 'confirm'],
            template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keyup.enter="$emit(\'confirm\')">',
          }),
        },
      },
    })
    const input = wrapper.get('input')
    await input.setValue(keyword.toLowerCase())
    await input.trigger('keyup.enter')
    expect(wrapper.emitted('confirm')).toBeUndefined()
    await input.setValue(keyword)
    await input.trigger('keyup.enter')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    expect((input.element as HTMLInputElement).value).toBe('')
    await input.trigger('keyup.enter')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    wrapper.unmount()
  })
})
