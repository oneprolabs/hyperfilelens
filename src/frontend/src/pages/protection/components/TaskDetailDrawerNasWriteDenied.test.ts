import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskDetailDrawer.vue'), 'utf8')

describe('TaskDetailDrawer NAS repository write denial', () => {
  it('uses the shared structured-error mapping for event failures', () => {
    expect(source).toContain("import { nasRepositoryFailureMessage } from '../../../lib/nasMountTroubleshooting'")
    expect(source).toContain('const display = nasRepositoryFailureMessage(code, message, t)')
    expect(source).not.toContain(':title="taskFailureMessage"')
  })

  it('does not repeat a backup failure detail on the Finalize terminal event', () => {
    expect(source).toContain("event.message === 'Task finished with status failed' && step?.step_name === 'finalize_snapshot'")
  })
})

describe('TaskDetailDrawer structured failure layout', () => {
  it('lets structured failure details use the unused event-time column', () => {
    expect(source).toContain("'hfl-task-drawer__event-row--failure-details': hasEventFailureDetails(event)")
    expect(source).toContain('.hfl-task-drawer__event-row--failure-details .hfl-task-drawer__event-content')
    expect(source).toContain('grid-template-columns: 16px minmax(0, 1fr);')
    expect(source).toContain('.hfl-task-drawer__event-row--failure-details .hfl-task-drawer__event-time')
  })
})
