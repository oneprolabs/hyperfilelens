// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Activity } from 'lucide-vue-next'
import { describe, expect, it } from 'vitest'
import HflTypeLabel from './HflTypeLabel.vue'

describe('HflTypeLabel', () => {
  it('keeps the icon to the left inside list tables', () => {
    const wrapper = mount({
      components: { Activity, HflTypeLabel },
      template: `
        <table class="hfl-list-table">
          <tbody>
            <tr>
              <td class="el-table__cell">
                <div class="cell">
                  <HflTypeLabel label="Metric" :icon="Activity" />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      `,
      setup() {
        return { Activity }
      },
    }, { attachTo: document.body })

    const label = wrapper.get('.hfl-table-type-label')
    const listStyles = readFileSync(resolve(process.cwd(), 'src/styles/list-page-ui.css'), 'utf8')
    expect(listStyles).toContain('> span:not(.el-tag):not(.hfl-table-type-label)')
    expect(listStyles).toMatch(/\.hfl-table-type-label\s*{[^}]*display:\s*inline-flex/s)
    expect(listStyles).toMatch(/\.hfl-table-type-label\s*{[^}]*max-width:\s*100%/s)
    expect(listStyles).toMatch(/\.hfl-table-type-label\s*{[^}]*line-height:\s*18px/s)
    expect(listStyles).toMatch(/\.hfl-table-type-label--primary\s*{[^}]*color:\s*var\(--color-text-primary[^}]*font-weight:\s*500/s)
    expect(listStyles).toMatch(/\.hfl-table-type-label--secondary\s*{[^}]*color:\s*var\(--color-text-secondary/s)
    expect(listStyles).toMatch(/\.hfl-table-type-label\s*>\s*span\s*{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s)
    expect(listStyles).toMatch(/\.hfl-table-type-label__icon\s*{[^}]*width:\s*13px/s)
    expect(label.element.children[0]?.tagName.toLowerCase()).toBe('svg')
    expect(label.element.children[1]?.textContent).toBe('Metric')
    wrapper.unmount()
  })

  it('supports explicit primary emphasis without changing the neutral default', () => {
    const secondary = mount(HflTypeLabel, { props: { label: 'Secondary' } })
    const primary = mount(HflTypeLabel, { props: { label: 'Primary', emphasis: 'primary' } })

    expect(secondary.get('.hfl-table-type-label').classes()).toContain('hfl-table-type-label--secondary')
    expect(primary.get('.hfl-table-type-label').classes()).toContain('hfl-table-type-label--primary')
  })
})
