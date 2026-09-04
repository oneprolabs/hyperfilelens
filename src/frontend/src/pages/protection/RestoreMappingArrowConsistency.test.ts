import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const pages = [
  ['create restore task', new URL('./DataProtection.vue', import.meta.url)],
  ['edit restore plan', new URL('./BackupCreateWizard.vue', import.meta.url)],
  ['source detail restore plan', new URL('./components/FlowBackupSourceDetailDrawer.vue', import.meta.url)],
] as const

const mappingArrowClass = /class="(?:create-recovery-plan-mapping__arrow|recovery-mapping-line__arrow)"/g
const mappingArrowElement = /<span\s+class="(?:create-recovery-plan-mapping__arrow|recovery-mapping-line__arrow)"\s+aria-hidden="true"\s*>([\s\S]*?)<\/span>/g

describe('restore mapping arrow consistency', () => {
  it.each(pages)('uses the shared ArrowRight icon in %s', (_name, file) => {
    const source = readFileSync(file, 'utf8')
    const arrowCount = source.match(mappingArrowClass)?.length || 0
    const arrowBodies = [...source.matchAll(mappingArrowElement)].map((match) => match[1]?.trim())

    expect(arrowCount).toBeGreaterThan(0)
    expect(arrowBodies).toHaveLength(arrowCount)
    expect(new Set(arrowBodies)).toEqual(new Set(['<ArrowRight :size="14" />']))
  })
})
