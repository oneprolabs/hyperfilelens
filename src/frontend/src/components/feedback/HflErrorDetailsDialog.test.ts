// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import { closeErrorDetails, openErrorDetails } from '../../lib/errors/details'
import HflErrorDetailsDialog from './HflErrorDetailsDialog.vue'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

describe('related task navigation', () => {
  afterEach(() => { closeErrorDetails(); push.mockClear() })

  it('hides only the current task link and preserves navigation from other contexts', async () => {
    const wrapper = mount(HflErrorDetailsDialog, {
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        stubs: {
          ElDialog: { template: '<div><slot name="header" /><slot /><slot name="footer" /></div>' },
          ElButton: { template: '<button><slot /></button>' },
        },
      },
    })
    const payload = { title: 'Failed', summary: 'Read failed', taskUuid: 'task-1' }
    const taskButton = () => wrapper.findAll('button').find(button => button.text() === en.feedback.errorDetails.openTask)
    openErrorDetails(payload, { currentTaskUuid: 'task-1' })
    await nextTick()
    expect(taskButton()).toBeUndefined()
    openErrorDetails(payload, { currentTaskUuid: 'task-2' })
    await nextTick()
    expect(taskButton()).toBeDefined()
    openErrorDetails(payload)
    await nextTick()
    await taskButton()!.trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/ops/jobs', query: { taskUuid: 'task-1' } })
    openErrorDetails({ title: 'Failed', summary: 'Read failed' })
    await nextTick()
    expect(taskButton()).toBeUndefined()
    wrapper.unmount()
  })
})
