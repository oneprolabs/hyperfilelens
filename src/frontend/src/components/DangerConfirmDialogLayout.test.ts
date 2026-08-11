// @vitest-environment jsdom
/* eslint-disable vue/one-component-per-file, vue/require-default-prop */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
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
      stubs: {
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
    expect(dialogSource).toContain(':title="item.status.label"')
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status\s*{[^}]*display:\s*inline-block;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status\s*{[^}]*max-width:\s*100%;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status\s*{[^}]*overflow:\s*hidden;/s)
    expect(dialogSource).toMatch(/\.hfl-danger-confirm__status\s*{[^}]*text-overflow:\s*ellipsis;/s)
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
