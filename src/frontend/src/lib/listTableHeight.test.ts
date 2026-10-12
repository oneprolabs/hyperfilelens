// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { listTableMaxHeight } from './listTableHeight'

function rect(element: HTMLElement, top: number, bottom: number) {
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ top, bottom, height: bottom - top } as DOMRect)
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('list table available height', () => {
  function setup() {
    const page = document.createElement('div')
    page.className = 'page-body'
    page.style.paddingBottom = '16px'
    page.style.borderBottom = '1px solid'
    const region = document.createElement('div')
    const footer = document.createElement('div')
    page.append(region, footer)
    document.body.append(page)
    rect(page, 100, 700)
    rect(region, 250, 600)
    rect(footer, 600, 648)
    return { page, region, footer }
  }

  it('subtracts outer padding and border exactly once, plus the actual footer height', () => {
    const { region, footer } = setup()
    expect(listTableMaxHeight(region, footer)).toBe(385)
  })

  it('reclaims removed footer padding and responds to wrapped pagination', () => {
    const { region, footer } = setup()
    rect(footer, 600, 664)
    expect(listTableMaxHeight(region, footer)).toBe(369)
    rect(footer, 600, 648)
    expect(listTableMaxHeight(region, footer)).toBe(385)
    rect(footer, 600, 692)
    expect(listTableMaxHeight(region, footer)).toBe(341)
  })

  it('does not force a 280px table when a short viewport has less room', () => {
    const { page, region, footer } = setup()
    rect(page, 100, 450)
    expect(listTableMaxHeight(region, footer)).toBe(135)
    rect(page, 100, 300)
    expect(listTableMaxHeight(region, footer)).toBe(1)
  })

  it('uses the viewport as a cap and a footer estimate only when no footer exists', () => {
    const { page, region, footer } = setup()
    rect(page, 100, 2000)
    expect(listTableMaxHeight(region, footer)).toBe(window.innerHeight - 250 - 48)
    expect(listTableMaxHeight(region, null, 48)).toBe(window.innerHeight - 250 - 48)
  })

  it('falls back to the viewport when there is no page container', () => {
    const region = document.createElement('div')
    rect(region, 250, 600)
    expect(listTableMaxHeight(region, null)).toBe(window.innerHeight - 250)
  })
})
