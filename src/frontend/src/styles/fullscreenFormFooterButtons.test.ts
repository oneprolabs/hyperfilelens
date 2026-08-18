import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const shell = readFileSync(resolve(process.cwd(), 'src/styles/fullscreen-form-shell.css'), 'utf8')
const tokens = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

type Rgb = [number, number, number]

function hexToken(name: string): Rgb {
  const value = tokens.match(new RegExp(`${name}:\\s*#([0-9a-f]{6})`, 'i'))?.[1]
  if (!value) throw new Error(`Missing color token: ${name}`)
  return [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16)) as Rgb
}

function blend(foreground: Rgb, background: Rgb, opacity: number): Rgb {
  return foreground.map((channel, index) => (
    Math.round(channel * opacity + background[index] * (1 - opacity))
  )) as Rgb
}

function luminance(color: Rgb) {
  const [red, green, blue] = color.map((channel) => {
    const normalized = channel / 255
    return normalized <= 0.03928
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue
}

function contrast(foreground: Rgb, background: Rgb) {
  const lighter = Math.max(luminance(foreground), luminance(background))
  const darker = Math.min(luminance(foreground), luminance(background))
  return (lighter + 0.05) / (darker + 0.05)
}

describe('fullscreen form footer buttons', () => {
  it('keeps secondary actions readable on hybrid and dark footer surfaces', () => {
    expect(shell).toContain('--fullscreen-form-secondary-text: var(--el-color-white)')
    expect(shell).toContain('--fullscreen-form-secondary-hover-text: var(--el-color-white)')
    expect(shell).toContain('--fullscreen-form-secondary-hover-border: var(--color-brand-violet-soft)')
    expect(shell).toContain(':not(.is-disabled):not(:disabled):hover,')
    expect(shell).toContain(':not(.is-disabled):not(:disabled):focus-visible {')
    expect(shell).toContain('background-color: var(--fullscreen-form-secondary-hover-bg) !important')
    expect(shell).toContain('color: var(--fullscreen-form-secondary-hover-text) !important')

    const hoverSurface = blend(hexToken('--color-primary'), [43, 45, 54], 0.32)
    expect(contrast([255, 255, 255], hoverSurface)).toBeGreaterThanOrEqual(4.5)
  })

  it('preserves a light-surface treatment for the light theme', () => {
    const start = shell.indexOf("html[data-theme='light'] .fullscreen-form-footer {")
    const end = shell.indexOf('\n}', start)
    const rule = shell.slice(start, end)

    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    expect(rule).toContain('--fullscreen-form-secondary-hover-bg: color-mix(in srgb, var(--color-primary) 8%, white)')
    expect(rule).toContain('--fullscreen-form-secondary-hover-text: var(--color-primary-hover)')
    expect(rule).toContain('--fullscreen-form-secondary-active-text: var(--color-primary-active)')

    const hoverSurface = blend(hexToken('--color-primary'), [255, 255, 255], 0.08)
    expect(contrast(hexToken('--color-primary-hover'), hoverSurface)).toBeGreaterThanOrEqual(4.5)
  })

  it('does not restyle typed or disabled buttons as secondary actions', () => {
    expect(shell).toContain(':not(.el-button--primary)')
    expect(shell).toContain(':not(.el-button--danger)')
    expect(shell).toContain(':not(.el-button--warning)')
    expect(shell).toContain(':not(.el-button--success)')
    expect(shell).toContain(':not(.el-button--info)')
    expect(shell).toContain('.is-disabled,')
    expect(shell).toContain(':disabled {')
    expect(shell).toContain('color: var(--fullscreen-form-secondary-disabled-text) !important')
  })
})
