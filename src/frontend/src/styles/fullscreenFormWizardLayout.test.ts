import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const shell = readFileSync(resolve(process.cwd(), 'src/styles/fullscreen-form-shell.css'), 'utf8')

describe('fullscreen wizard layout', () => {
  it('lets the page reserve fixed-footer space without duplicating it in the wizard main', () => {
    const start = shell.indexOf('.fullscreen-form-main--wizard {')
    const end = shell.indexOf('\n}', start)
    const rule = shell.slice(start, end)

    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    expect(rule).toContain('height: 100%;')
    expect(rule).toContain('box-sizing: border-box;')
    expect(rule).toContain('scroll-padding: 16px 0;')
    expect(rule).not.toContain('padding-bottom')
    expect(rule).not.toContain('84px')
  })
})
