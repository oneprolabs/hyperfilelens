import { describe, expect, it } from 'vitest'
import { copilotExecutionScope } from './copilotExecutionScope'

describe('Copilot execution identity', () => {
  it('prefers delegated Run identity over display names', () => {
    expect(copilotExecutionScope({ delegated_run_uuid: 'one', assistant_name: 'Reader' }))
      .toBe(copilotExecutionScope({ delegatedRunUuid: 'one', assistantName: 'Renamed' }))
    expect(copilotExecutionScope({ delegatedRunUuid: 'one' }))
      .not.toBe(copilotExecutionScope({ delegatedRunUuid: 'two' }))
  })

  it('uses the same assistant/task fallback for live events and flattened history', () => {
    expect(copilotExecutionScope({ assistantName: 'Reader', delegatedTask: 'Read documents' }))
      .toBe(copilotExecutionScope({ assistant_name: 'Reader', delegated_task: 'Read documents' }))
    expect(copilotExecutionScope({ assistantName: 'Reader', delegatedTask: 'First task' }))
      .not.toBe(copilotExecutionScope({ assistantName: 'Reader', delegatedTask: 'Second task' }))
    expect(copilotExecutionScope({})).not.toBe(copilotExecutionScope({ assistantName: 'Reader' }))
  })
})
