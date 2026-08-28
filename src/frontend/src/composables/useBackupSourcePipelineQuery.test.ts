import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/composables/useBackupSourcePipeline.ts'), 'utf8')

describe('backup source pipeline query cost', () => {
  it('refreshes pipeline membership statistics with count-only requests', () => {
    expect(source).toContain('step: 2, page: 1, page_size: 1')
    expect(source).toContain('step: 3, page: 1, page_size: 1')
    expect(source).not.toContain('const pageSize = 100')
    expect(source).not.toContain('while (rows.length < total)')
    expect(source).not.toContain('pipelineStep2Ids')
    expect(source).not.toContain('pipelineStep3Ids')
  })
})
