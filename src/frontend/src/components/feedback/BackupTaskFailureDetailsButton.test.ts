// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it } from 'vitest'
import { en } from '../../locales/en'
import type { TaskRow } from '../../lib/taskApi'
import { closeErrorDetails, errorDetailsState } from '../../lib/errors/details'
import BackupTaskFailureDetailsButton from './BackupTaskFailureDetailsButton.vue'

describe('backup task details entry', () => {
  afterEach(closeErrorDetails)
  it('opens persisted diagnostics and removes the entry after recovery', async () => {
    const task = { task_type: 'backup', task_uuid: 'task-1', status: 'failed', error_message: 'Read failed', result_payload: { failure_details: { category: 'source_read_failed', count: 1, items: [{ path: '/data/file' }] } } } as TaskRow
    const wrapper = mount(BackupTaskFailureDetailsButton, {
      props: { task },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        stubs: { ElButton: { template: '<button><slot /></button>' } },
      },
    })
    await wrapper.get('button').trigger('click')
    expect(errorDetailsState.current?.taskUuid).toBe('task-1')
    expect(errorDetailsState.current?.entities?.[0].name).toBe('/data/file')
    await wrapper.setProps({ task: { ...task, status: 'success', result_payload: {} } })
    expect(wrapper.find('button').exists()).toBe(false)
  })
})
