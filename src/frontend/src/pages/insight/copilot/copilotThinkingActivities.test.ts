import { describe, expect, it } from 'vitest'
import {
  hasStructuredRuntimeContent,
  summarizeThinkingSteps,
  thinkingActivityCount,
} from './copilotThinkingActivities'

describe('copilot thinking activity aggregation', () => {
  it('hides runtime bootstrap events and merges tool start/done pairs', () => {
    const activities = summarizeThinkingSteps([
      { message: 'deepagents.runtime.start', agentEvent: 'deepagents.runtime.start' },
      { message: 'deepagents.agent.invoke', agentEvent: 'deepagents.agent.invoke' },
      { message: 'phase.changed', eventType: 'phase.changed' },
      { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
      { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
      { message: 'deepagents.runtime.done', agentEvent: 'deepagents.runtime.done' },
      { message: 'deepagents.runtime.error', agentEvent: 'deepagents.runtime.error', error: 'Run failed' },
    ])

    expect(activities.map((activity) => [activity.title, activity.count])).toEqual([
      ['Searching relevant sources', 1],
    ])
    expect(thinkingActivityCount([
      { message: 'deepagents.runtime.start', agentEvent: 'deepagents.runtime.start' },
      { message: 'deepagents.agent.create', agentEvent: 'deepagents.agent.create' },
    ])).toBe(0)
  })

  it('shows repeated retrievals as one activity with a count', () => {
    const activities = summarizeThinkingSteps([
      { message: 'tool.search_workspace.start', agentEvent: 'tool.search_workspace.start' },
      { message: 'tool.search_workspace.done', agentEvent: 'tool.search_workspace.done' },
      { message: 'tool.find_files.invoke', agentEvent: 'tool.find_files.invoke' },
      { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
      { message: 'deepagents.runtime.done', agentEvent: 'deepagents.runtime.done' },
    ])

    expect(activities.find((activity) => activity.title === 'Searching relevant sources')).toMatchObject({
      count: 2,
      status: 'completed',
    })
    expect(thinkingActivityCount([
      { message: 'tool.search_workspace.start', agentEvent: 'tool.search_workspace.start' },
      { message: 'tool.search_workspace.done', agentEvent: 'tool.search_workspace.done' },
      { message: 'tool.find_files.invoke', agentEvent: 'tool.find_files.invoke' },
      { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
    ])).toBe(2)
  })

  it('uses SourceLens activity descriptions for common tools', () => {
    const activities = summarizeThinkingSteps([
      { message: 'tool.read_workspace_file.start', agentEvent: 'tool.read_workspace_file.start' },
      { message: 'tool.read_workspace_file.done', agentEvent: 'tool.read_workspace_file.done' },
      { message: 'tool.run_skill_script.start', agentEvent: 'tool.run_skill_script.start' },
    ])

    expect(activities.map((activity) => activity.title)).toEqual([
      'Reading relevant sources',
      'Querying business data',
    ])
  })

  it('recognizes structured runtime events without a legacy activity count', () => {
    const steps = [{
      eventType: 'plan.updated',
      message: 'plan.updated',
      payload: { steps: [{ id: 'step-1', title: 'Inspect sources' }] },
    }]

    expect(thinkingActivityCount(steps)).toBe(0)
    expect(hasStructuredRuntimeContent(steps)).toBe(true)
  })

  it('does not treat bare activity.* events as Agent-activity content', () => {
    const steps = [{
      eventType: 'activity.recorded',
      message: 'activity.recorded',
      payload: { id: 'act-1', kind: 'searching_sources', status: 'in_progress' },
    }]

    expect(thinkingActivityCount(steps)).toBe(0)
    expect(hasStructuredRuntimeContent(steps)).toBe(false)
  })

  it('keeps tool activities when structured runtime events are also present', () => {
    const activities = summarizeThinkingSteps([
      {
        eventType: 'plan.updated',
        message: 'plan.updated',
        payload: { steps: [{ id: 'step-1', title: 'Inspect sources' }] },
      },
      { message: 'tool.find_files.start', agentEvent: 'tool.find_files.start' },
      { message: 'tool.find_files.done', agentEvent: 'tool.find_files.done' },
    ])

    expect(activities).toHaveLength(1)
    expect(activities[0]).toMatchObject({
      title: 'Searching relevant sources',
      status: 'completed',
    })
  })
})
