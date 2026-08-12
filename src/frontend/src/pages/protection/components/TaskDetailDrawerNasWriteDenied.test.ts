import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskDetailDrawer.vue'), 'utf8')

describe('TaskDetailDrawer NAS repository write denial', () => {
  it('uses the shared structured-error mapping for task and event failures', () => {
    expect(source).toContain("import { nasRepositoryFailureMessage } from '../../../lib/nasMountTroubleshooting'")
    expect(source).toContain('const taskFailureMessage = computed(() => nasRepositoryFailureMessage(')
    expect(source).toContain('const display = nasRepositoryFailureMessage(code, message, t)')
  })
})
