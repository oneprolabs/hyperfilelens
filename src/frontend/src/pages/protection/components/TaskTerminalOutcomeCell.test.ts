// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import TaskTerminalOutcomeCell from './TaskTerminalOutcomeCell.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      ops: {
        task: {
          status: {
            success: 'Succeeded',
            failed: 'Failed',
            timeout: 'Timed out',
            partial: 'Partial',
            cancelled: 'Cancelled',
          },
          failureDetails: {
            summary: {
              mixed_source_errors: '{count} source items could not be processed.',
            },
          },
        },
      },
    },
  },
})

function mountCell(task: Record<string, unknown>, fallback?: Record<string, unknown>) {
  return mount(TaskTerminalOutcomeCell, {
    props: { task, fallback },
    global: { plugins: [i18n] },
  })
}

describe('TaskTerminalOutcomeCell', () => {
  it.each([
    ['success', 'Succeeded', 'success', false],
    ['failed', 'Failed', 'danger', true],
    ['timeout', 'Timed out', 'danger', true],
    ['partial', 'Partial', 'warning', true],
    ['cancelled', 'Cancelled', 'neutral', false],
  ])('renders the %s terminal outcome with the expected tone', (status, label, tone, hasDiagnostic) => {
    const wrapper = mountCell({
      status,
      finished_at: '2026-07-31T10:24:00',
      error_code: 'CONNECTION_REFUSED',
      error_message: 'Connection refused',
    })

    expect(wrapper.get('.task-terminal-outcome__status').text()).toBe(label)
    expect(wrapper.get('.task-terminal-outcome__time').text()).toBe('2026-07-31 10:24')
    expect(wrapper.get('.task-terminal-outcome').classes()).toContain(`task-terminal-outcome--${tone}`)
    expect(wrapper.find('.task-terminal-outcome__diagnostic').exists()).toBe(hasDiagnostic)
    expect(wrapper.get('.task-terminal-outcome').element.children.length).toBe(hasDiagnostic ? 2 : 1)
  })

  it('renders one compact diagnostic line with the full value available on hover', () => {
    const wrapper = mountCell({
      status: 'failed',
      finished_at: '2026-07-31T10:24:00',
      error_code: 'CONNECTION_REFUSED',
      error_message: 'Connection refused',
    })
    const diagnostic = wrapper.get('.task-terminal-outcome__diagnostic')

    expect(diagnostic.text()).toBe('[CONNECTION_REFUSED]Connection refused')
    expect(diagnostic.attributes('aria-label')).toBe('[CONNECTION_REFUSED] Connection refused')
    expect(diagnostic.attributes('data-table-overflow-title')).toBe('[CONNECTION_REFUSED] Connection refused')
    expect(diagnostic.attributes('data-table-overflow-tone')).toBe('danger')
    expect(wrapper.get('.task-terminal-outcome__code').text()).toBe('[CONNECTION_REFUSED]')
    expect(wrapper.get('.task-terminal-outcome__reason').text()).toBe('Connection refused')
  })

  it('uses timestamp fallbacks and never renders empty code brackets', () => {
    const wrapper = mountCell({
      status: 'timeout',
      started_at: '2026-07-31T10:24:00',
      error_message: 'Operation exceeded its deadline',
    })

    expect(wrapper.get('.task-terminal-outcome__time').text()).toBe('2026-07-31 10:24')
    expect(wrapper.find('.task-terminal-outcome__code').exists()).toBe(false)
    expect(wrapper.get('.task-terminal-outcome__reason').text()).toBe('Operation exceeded its deadline')
  })

  it('uses a terminal fallback when the primary task is not terminal', () => {
    const wrapper = mountCell(
      { status: 'running' },
      { status: 'available', created_at: '2026-07-31T10:24:00' },
    )

    expect(wrapper.get('.task-terminal-outcome__status').text()).toBe('Succeeded')
    expect(wrapper.get('.task-terminal-outcome__time').text()).toBe('2026-07-31 10:24')
  })

  it('fills missing task diagnostics from the matching fallback record', () => {
    const wrapper = mountCell(
      { status: 'failed' },
      {
        status: 'failed',
        finished_at: '2026-07-31T10:24:00',
        error_code: 'CONNECTION_REFUSED',
        error_message: 'Connection refused',
      },
    )

    expect(wrapper.get('.task-terminal-outcome__time').text()).toBe('2026-07-31 10:24')
    expect(wrapper.get('.task-terminal-outcome__diagnostic').attributes('aria-label'))
      .toBe('[CONNECTION_REFUSED] Connection refused')
  })

  it('prefers a structured failure summary from recent task events', () => {
    const wrapper = mountCell({
      status: 'failed',
      error_code: 'KOPIA_SNAPSHOT_FATAL',
      error_message: 'raw truncated output',
      recent_events: [{
        metadata: {
          failure_details: {
            category: 'mixed_source_errors',
            total_count: 795,
          },
        },
      }],
    })

    expect(wrapper.get('.task-terminal-outcome__reason').text()).toBe('795 source items could not be processed.')
    expect(wrapper.get('.task-terminal-outcome__diagnostic').attributes('aria-label'))
      .toBe('[KOPIA_SNAPSHOT_FATAL] 795 source items could not be processed.')
  })

  it('uses the approved semantic tokens and font weights', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskTerminalOutcomeCell.vue'), 'utf8')

    expect(source).toMatch(/task-terminal-outcome__status[\s\S]*?font-weight: 600;/)
    expect(source).toMatch(/task-terminal-outcome__code[\s\S]*?font-weight: 500;/)
    expect(source).toContain('var(--color-success-text)')
    expect(source).toContain('var(--color-error-text)')
    expect(source).toContain('var(--color-warning-text)')
    expect(source).toContain('var(--color-text-secondary)')
    expect(source).toContain('var(--color-text-primary)')
  })

  it('exposes warning semantics to the shared overflow tooltip', () => {
    const wrapper = mountCell({
      status: 'partial',
      error_code: 'MIXED_SOURCE_ERRORS',
      error_message: 'Some source items could not be processed',
    })

    expect(wrapper.get('.task-terminal-outcome__diagnostic').attributes('data-table-overflow-tone'))
      .toBe('warning')
  })
})
