import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SOURCE_TAG_COLORS, sourceTagColor } from './sourceTagColor'

describe('source tag palette', () => {
  it('restricts unknown colors to the neutral fallback', () => {
    expect(SOURCE_TAG_COLORS).toContain('blue')
    expect(sourceTagColor('blue')).toBe('blue')
    expect(sourceTagColor('#ff0000')).toBe('neutral')
    expect(sourceTagColor('red; background: black')).toBe('neutral')
  })

  it('uses one badge and palette in all source surfaces', () => {
    const sources = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupSources.vue'), 'utf8')
    const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
    const badge = readFileSync(resolve(process.cwd(), 'src/components/SourceTagBadge.vue'), 'utf8')
    const palette = readFileSync(resolve(process.cwd(), 'src/styles/source-tag-ui.css'), 'utf8')
    expect(sources).toContain('<SourceTagBadge')
    expect(wizard).toContain('<SourceTagBadge')
    expect(badge).toContain(':data-color="sourceTagColor(tag.color)"')
    expect(badge).toContain('import { Tag } from')
    expect(badge.match(/<Tag\s/g)?.length).toBe(2)
    expect(badge).not.toContain('source-tag-badge__dot')
    expect(palette).toContain('.source-tag-swatch__dot')
    expect(palette).toContain('.source-tag-badge__label {\n  min-width: 0;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;')
    expect(palette).toContain("html[data-theme='dark'] .source-tag-badge")
  })
})
