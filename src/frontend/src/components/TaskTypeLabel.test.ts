// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import TaskTypeLabel from './TaskTypeLabel.vue'

function mountLabel(props: {
  type?: string
  operationType?: string
  emphasis?: 'primary' | 'secondary'
} = {}) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: {
      en: {
        ops: {
          task: {
            emptyMark: '—',
            taskType: {
              backup: 'Backup',
              insight_workspace_restore: 'Insight Workspace Restore',
              repository_operation: 'Repository Operation',
            },
            operationType: {
              maintenanceQuick: 'Quick Maintenance',
              maintenanceFull: 'Full Maintenance',
              cleanupTarget: 'Delete Subrepository',
              cleanupRepository: 'Delete Repository',
              check: 'Repository Check',
            },
          },
        },
      },
    },
  })

  return mount(TaskTypeLabel, {
    props: { type: 'backup', ...props },
    global: { plugins: [i18n] },
  })
}

describe('TaskTypeLabel', () => {
  it('uses primary emphasis for task identity by default', () => {
    const wrapper = mountLabel()

    expect(wrapper.get('.hfl-table-type-label').classes()).toContain('hfl-table-type-label--primary')
    expect(wrapper.text()).toBe('Backup')
  })

  it('can be reduced to secondary emphasis in supporting contexts', () => {
    const wrapper = mountLabel({ emphasis: 'secondary' })

    expect(wrapper.get('.hfl-table-type-label').classes()).toContain('hfl-table-type-label--secondary')
  })

  it.each([
    ['maintenance.quick', 'Quick Maintenance'],
    ['maintenance.full', 'Full Maintenance'],
    ['cleanup.target', 'Delete Subrepository'],
    ['cleanup.repository', 'Delete Repository'],
    ['check', 'Repository Check'],
  ])('shows repository operation %s as secondary text', (operationType, expected) => {
    const wrapper = mountLabel({ type: 'repository_operation', operationType })

    expect(wrapper.get('.hfl-table-type-label').text()).toBe('Repository Operation')
    expect(wrapper.get('.hfl-task-type-stack__operation').text()).toBe(expected)
  })

  it('formats an unknown repository operation as a readable label', () => {
    const wrapper = mountLabel({ type: 'repository_operation', operationType: 'repair.index' })

    expect(wrapper.get('.hfl-task-type-stack__operation').text()).toBe('Repair Index')
  })

  it('keeps the type on one line when the operation is missing', () => {
    const wrapper = mountLabel({ type: 'repository_operation' })

    expect(wrapper.find('.hfl-task-type-stack').exists()).toBe(false)
    expect(wrapper.text()).toBe('Repository Operation')
  })

  it('uses the restore icon for insight workspace restores', () => {
    const wrapper = mountLabel({ type: 'insight_workspace_restore' })

    expect(wrapper.text()).toBe('Insight Workspace Restore')
    expect(wrapper.find('svg').exists()).toBe(true)
  })
})
