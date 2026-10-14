// @vitest-environment jsdom
import type { App, Directive } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { setupTableOverflowTitleDirective } from './tableOverflowTitle'

describe('Dialog status tag overflow tooltip', () => {
  it.each(['Offline', 'proxy-host-complete-name', '/repository/path/full-value'])('only shows a tooltip when %s is clipped', (value) => {
    vi.useFakeTimers()
    let directive!: Directive<HTMLElement>
    setupTableOverflowTitleDirective({ directive(name: string, value: Directive<HTMLElement>) {
      if (name === 'table-overflow-title') directive = value
    } } as unknown as App)
    const table = document.createElement('div')
    table.className = 'el-table'
    const cell = document.createElement('div')
    cell.className = 'el-table__cell'
    const body = document.createElement('div')
    body.className = 'cell'
    const tag = document.createElement('span')
    tag.className = 'el-tag'
    tag.setAttribute('data-table-overflow-explicit-only', '')
    const text = document.createElement('span')
    text.innerText = value
    text.dataset.tableOverflowTitle = value
    text.setAttribute('data-table-overflow-ellipsis-only', '')
    text.style.overflow = 'hidden'
    let width = 40
    Object.defineProperties(text, {
      clientWidth: { value: 40 },
      clientHeight: { value: 18 },
      scrollHeight: { value: 22 },
      scrollWidth: { get: () => width },
    })
    // A tag wrapper may report overflow due to border/line-height; this
    // must not create a tooltip while the label itself fits.
    Object.defineProperties(tag, { clientHeight: { value: 18 }, scrollHeight: { value: 20 } })
    Object.defineProperties(body, { clientWidth: { value: 100 }, scrollWidth: { value: 110 }, clientHeight: { value: 18 }, scrollHeight: { value: 22 } })
    tag.append(text)
    body.append(tag)
    cell.append(body)
    table.append(cell)
    document.body.append(table)
    directive.mounted?.(table, {} as never, {} as never, {} as never)
    text.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    vi.advanceTimersByTime(300)
    expect(document.querySelector<HTMLElement>('#hfl-table-overflow-tooltip')?.style.display || 'none').toBe('none')
    text.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
    vi.advanceTimersByTime(300)
    width = 90
    text.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    vi.advanceTimersByTime(300)
    const tooltip = document.querySelector<HTMLElement>('#hfl-table-overflow-tooltip')
    expect(tooltip?.style.display).toBe('block')
    expect(tooltip?.textContent).toBe(value)
    expect(document.querySelectorAll('#hfl-table-overflow-tooltip')).toHaveLength(1)
    expect(tag.hasAttribute('title')).toBe(false)
    directive.unmounted?.(table, {} as never, {} as never, {} as never)
    table.remove()
    vi.useRealTimers()
  })
})
