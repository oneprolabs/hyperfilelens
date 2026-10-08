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

  it('anchors fallback failures to a step actually marked failed', () => {
    expect(source).toContain("step.status === 'failed' || step.status === 'timeout'")
    expect(source).toContain('failedSteps.find(step => step.step_name === failedStep)?.id')
    expect(source).toContain('fallbackFailureStepId === step.id')
    expect(source).not.toContain("terminalErrorCode === 'KOPIA_PROCESS_DIED'")
    expect(source).not.toContain('step.step_name === currentStep')
  })

  it('does not add a second step-level failure panel when the directory event already has an error', () => {
    expect(source).toContain("message === 'Directory backup failed'")
    expect(source).toContain("metadata.error_message || metadata.error_code")
  })
})

describe('TaskDetailDrawer structured detail layout', () => {
  it('lets failure and skipped-item details use the unused event-time column', () => {
    expect(source).toContain("['failure_details', 'skipped_details']")
    expect(source).toContain("'hfl-task-drawer__event-row--detail-panel': hasEventDetailPanel(event)")
    expect(source).toContain('.hfl-task-drawer__event-row--detail-panel .hfl-task-drawer__event-content')
    expect(source).toContain('grid-template-columns: 16px minmax(0, 1fr);')
    expect(source).toContain('.hfl-task-drawer__event-row--detail-panel .hfl-task-drawer__event-time')
  })
})

describe('TaskEventFailureDetails panel styling', () => {
  const failureSource = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskEventFailureDetails.vue'), 'utf8')

  it('keeps the nested border only for mixed failure and skipped-warning panels', () => {
    const terminal = failureSource.match(/\.task-event-failure__terminal-box\s*\{([^}]*)\}/)?.[1]
    const mixed = failureSource.match(/\.task-event-failure--mixed \.task-event-failure__terminal-box\s*\{([^}]*)\}/)?.[1]
    expect(terminal).toBeDefined()
    expect(terminal).not.toContain('border:')
    expect(mixed).toContain('border:')
  })
})
