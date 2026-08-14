import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/components/WizardSteps.vue'), 'utf8')

describe('WizardSteps responsive horizontal variant', () => {
  it('keeps the horizontal layout opt-in', () => {
    expect(source).toContain('responsiveHorizontal?: boolean')
    expect(source).toContain('responsiveHorizontal: false')
    expect(source).toContain("'wizard-steps--responsive-horizontal': responsiveHorizontal")
  })

  it('uses a full horizontal stepper on narrow screens', () => {
    expect(source).toMatch(/@media \(max-width: 767\.98px\) \{[\s\S]*?\.wizard-steps--responsive-horizontal \{[\s\S]*?flex-direction: row;/)
    expect(source).toMatch(/\.wizard-steps--responsive-horizontal \.wizard-steps__connector \{[\s\S]*?height: 1px;/)
  })

  it('uses a compact status below phone width without horizontal scrolling', () => {
    expect(source).toContain('{{ activeStepIndex + 1 }}/{{ steps.length }} · {{ activeStepLabel }}')
    expect(source).toMatch(/@media \(max-width: 479\.98px\) \{[\s\S]*?\.wizard-steps--responsive-horizontal \.wizard-steps__label \{[\s\S]*?display: none;/)
    expect(source).toMatch(/\.wizard-steps--responsive-horizontal \.wizard-steps__compact-status \{[\s\S]*?display: block;/)
  })
})
