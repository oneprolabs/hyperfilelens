import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/pages/insight/AiModelFormPage.vue'), 'utf8')
const tagStyle = source.match(/\.ai-cap-tag\s*{([^}]+)}/)?.[1] || ''

describe('AI model form capability tags', () => {
  it('uses small rectangular tags without decorative status dots', () => {
    expect(tagStyle).toContain('border-radius: 4px;')
    expect(tagStyle).not.toContain('999px')
    expect(source).not.toMatch(/\.ai-cap-tag::(?:before|after)/)
  })

  it('keeps capability colors and wrapped layout unchanged', () => {
    expect(tagStyle).toContain('var(--ai-capability-color')
    expect(tagStyle).toContain('font-size: 12px;')
    expect(source).toMatch(/\.ai-model-dropdown__caps\s*{[^}]*flex-wrap: wrap;/)
    for (const tone of ['sky', 'emerald', 'violet', 'indigo', 'rose', 'teal', 'gray']) {
      expect(source).toContain(`.cap-${tone} { --ai-capability-color:`)
    }
  })

  it('applies the same tag style to model choices and selected model capabilities', () => {
    expect(source.match(/class="ai-cap-tag"/g)).toHaveLength(2)
    expect(source).toContain('v-for="cap in modelCapabilities(model)"')
    expect(source).toContain('v-for="cap in selectedModelInfo.capabilities"')
  })
})
