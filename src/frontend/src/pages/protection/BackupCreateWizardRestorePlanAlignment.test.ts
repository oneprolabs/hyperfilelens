import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')

describe('restore plan mapping alignment', () => {
  it('centers the mapping index and delete action on the input control row', () => {
    expect(wizard).toMatch(/\.create-recovery-dir-plan-row__index \{[\s\S]*?align-self: start;[\s\S]*?height: 22px;[\s\S]*?margin-top: 6px;/)
    expect(wizard).toMatch(/\.create-recovery-dir-plan-cell--actions \{[\s\S]*?min-height: 34px;[\s\S]*?align-items: center;[\s\S]*?justify-content: center;/)
  })
})
