import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import { compactSourceText } from '../test/sourceText'

const taskDetailSurfaces = [
  {
    name: 'operations task detail',
    path: 'src/pages/ops/Tasks.vue',
    disabledClass: 'hfl-task-drawer__step-card-head--disabled',
  },
  {
    name: 'shared task detail drawer',
    path: 'src/pages/protection/components/TaskDetailDrawer.vue',
    disabledClass: 'hfl-task-drawer__step-card-head--disabled',
  },
  {
    name: 'flow source task detail drawer',
    path: 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue',
    disabledClass: 'dp-task-detail__step-card-head--disabled',
  },
] as const

describe.each(taskDetailSurfaces)('$name empty-event step expansion', ({ path, disabledClass }) => {
  const source = compactSourceText(readFileSync(resolve(process.cwd(), path), 'utf8'))

  it('keeps the step collapsed and exposes disabled semantics', () => {
    const toggleStart = source.indexOf('function toggleStep(stepId: number | string, eventCount: number)')
    const toggleEnd = source.indexOf('function setAllStepsExpanded', toggleStart)
    expect(toggleStart).toBeGreaterThanOrEqual(0)
    expect(toggleEnd).toBeGreaterThan(toggleStart)

    const toggleStep = source.slice(toggleStart, toggleEnd)
    expect(toggleStep).toContain('if (eventCount === 0) return')
    expect(toggleStep.indexOf('return')).toBeLessThan(toggleStep.indexOf('expanded'))
    expect(source).toContain(`'${disabledClass}': step.events.length === 0`)
    expect(source).toContain(':aria-expanded="step.events.length > 0 && isStepExpanded(step.id)"')
    expect(source).toContain(':aria-disabled="step.events.length === 0"')
    expect(source).toContain('@click="toggleStep(step.id, step.events.length)"')
  })

  it('shows a disabled right chevron with the shared tooltip', () => {
    expect(source).toContain('<ElTooltip ')
    expect(source).toContain('v-if="step.events.length === 0"')
    expect(source).toContain(':content="t(\'ops.task.emptyEvents\')"')
    expect(source).toContain('teleported')
    expect(source).toContain('append-to="body"')
    expect(source).toContain(':z-index="3600"')
    expect(source).toContain('class="hfl-task-step-chevron hfl-task-step-chevron--disabled"')
    expect(source).toContain('<ChevronDown v-else-if="isStepExpanded(step.id)"')
  })
})

it('uses concise shared copy for the empty-event tooltip', () => {
  expect(en.ops.task.emptyEvents).toBe('No events are available for this step.')
})
