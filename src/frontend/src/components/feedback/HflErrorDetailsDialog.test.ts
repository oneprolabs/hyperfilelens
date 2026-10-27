// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import { closeErrorDetails, errorDetailsState, openErrorDetails } from '../../lib/errors/details'
import type { ErrorDetailsPayload } from '../../lib/errors/details'
import HflErrorDetailsDialog from './HflErrorDetailsDialog.vue'
import { notificationTestFailureDetails } from '../../lib/notificationTestFailure'

const { push, resolve } = vi.hoisted(() => ({
  push: vi.fn(),
  resolve: vi.fn(({ path, query }) => ({ href: query.focus
    ? `${path}?focus=${encodeURIComponent(query.focus)}`
    : `${path}?taskUuid=${encodeURIComponent(query.taskUuid)}` })),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push, resolve }) }))
vi.mock('../../lib/api', () => ({ apiErrorMessageI18n: () => 'Request failed' }))
vi.mock('../../lib/taskDetailsLifecycle', () => ({ refreshTaskDetails: vi.fn(async (details) => details) }))
vi.mock('../../lib/unregisterFailureDetails', () => ({
  mergeUnregisterDetails: (_t: unknown, items: ErrorDetailsPayload[]) => ({
    ...items[0], taskUuid: undefined,
    relatedTasks: items.map(item => ({ taskUuid: item.taskUuid })),
  }),
}))

function mountDialog() {
  return mount(HflErrorDetailsDialog, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElDialog: { template: '<div><slot name="header" /><slot /><slot name="footer" /></div>' },
        ElButton: { template: '<button><slot /></button>' },
      },
    },
  })
}

describe('notification rejection presentation', () => {
  afterEach(() => closeErrorDetails())

  it('renders Feishu keyword reasons and remedies in the shared error sections', async () => {
    const wrapper = mountDialog()
    const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
    openErrorDetails(notificationTestFailureDetails({
      status: 400,
      errorCode: 'NOTIFICATION.FEISHU_REJECTED',
      detail: {
        code: 'NOTIFICATION.FEISHU_REJECTED',
        error: 'Feishu rejected the message (code=19024): Key Words Not Found',
      },
    }, (key) => i18n.global.t(key)))
    await flushPromises()
    const reasons = wrapper.get('.hfl-error-details__list')
    const resolutions = wrapper.get('.hfl-error-details__list--resolve')
    expect(reasons.text()).toContain(en.ops.notification.feishuKeywordReason)
    expect(resolutions.text()).toContain(en.ops.notification.feishuKeywordResolution)
    expect(wrapper.text()).toContain(en.feedback.errorDetails.reasons)
    expect(wrapper.text()).toContain(en.feedback.errorDetails.resolutions)
    wrapper.unmount()
  })
})

describe('related task navigation', () => {
  afterEach(() => { closeErrorDetails(); push.mockClear(); resolve.mockClear() })

  it('keeps a long repository list in a named keyboard-accessible scroll region', async () => {
    const wrapper = mountDialog()
    openErrorDetails({
      title: 'Incomplete usage', summary: 'Incomplete usage',
      errorCode: 'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE',
      entities: Array.from({ length: 20 }, (_, index) => ({
        type: 'repository' as const, id: index + 1, name: `Repository ${index + 1}`,
      })),
    })
    await flushPromises()
    const list = wrapper.get('.hfl-error-details__entities')
    expect(list.classes()).toContain('scrollbar')
    expect(list.attributes('tabindex')).toBe('0')
    expect(list.attributes('aria-label')).toBe(en.feedback.errorDetails.affectedEntities)
    expect(list.findAll('li')).toHaveLength(20)
    expect(list.findAll('.hfl-error-details__entity-link')).toHaveLength(20)
    expect(list.find('.hfl-error-details__task-link').exists()).toBe(false)
    wrapper.unmount()
  })

  it('links a visible blocking repository without closing the backup failure details', async () => {
    const wrapper = mountDialog()
    openErrorDetails({
      title: 'Incomplete organization usage', summary: 'Incomplete organization usage',
      errorCode: 'SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE',
      entities: [{ type: 'repository', id: 7, name: 'Unmeasured NAS' }],
    })
    await flushPromises()
    expect(wrapper.get('a').attributes()).toMatchObject({
      href: '/node/repositories?focus=7', target: '_blank', rel: 'noopener noreferrer',
      'aria-label': 'Open repository Unmeasured NAS in a new tab',
    })
    expect(errorDetailsState.current).not.toBeNull()
    expect(push).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('opens a single task in a new tab without navigating or closing the details', async () => {
    const wrapper = mountDialog()
    const payload = { title: 'Failed', summary: 'Read failed', taskUuid: 'task-1' }
    openErrorDetails(payload, { currentTaskUuid: 'task-1' })
    await nextTick()
    expect(wrapper.find('a').exists()).toBe(false)
    openErrorDetails(payload, { currentTaskUuid: 'task-2' })
    await flushPromises()
    const link = wrapper.get('a')
    expect(link.attributes()).toMatchObject({ href: '/ops/tasks?taskUuid=task-1', target: '_blank', rel: 'noopener noreferrer' })
    expect(link.attributes('aria-label')).toBe('Open task task-1 in a new tab')
    await link.trigger('click')
    expect(push).not.toHaveBeenCalled()
    expect(errorDetailsState.current?.summary).toBe('Read failed')
    openErrorDetails({ title: 'Failed', summary: 'Read failed' })
    await flushPromises()
    expect(wrapper.find('a').exists()).toBe(false)
    wrapper.unmount()
  })

  it('moves multiple tasks into ordered body rows and deduplicates their links', async () => {
    const wrapper = mountDialog()
    const uuids = [
      'a1e06de9-9b8d-56ea-bc4b-fed1cba3a5d5',
      'd06d2127-db42-5e43-8837-302f9cee882f',
      '1b83da10-7d62-5860-a078-12f9e3bb60cc',
    ]
    openErrorDetails({ title: 'Batch failed', summary: 'Batch failed', relatedTasks: [...uuids, uuids[0]].map(taskUuid => ({ taskUuid })) })
    await flushPromises()
    const rows = wrapper.findAll('.hfl-error-details__task-row')
    expect(rows).toHaveLength(3)
    expect(rows.map(row => row.get('code').text())).toEqual(uuids)
    rows.forEach(row => expect(row.attributes()).toMatchObject({ target: '_blank', rel: 'noopener noreferrer' }))
    expect(wrapper.find('.hfl-error-details__footer a').exists()).toBe(false)
    await rows[1].trigger('click')
    expect(push).not.toHaveBeenCalled()
    expect(errorDetailsState.current).not.toBeNull()
    wrapper.unmount()
  })

  it('excludes the current task from a multi-task list', async () => {
    const wrapper = mountDialog()
    openErrorDetails({ title: 'Batch', summary: 'Batch', relatedTasks: [{ taskUuid: 'current' }, { taskUuid: 'other' }] }, { currentTaskUuid: 'current' })
    await flushPromises()
    expect(wrapper.findAll('a')).toHaveLength(1)
    expect(wrapper.get('a').attributes('href')).toBe('/ops/tasks?taskUuid=other')
    wrapper.unmount()
  })
})

describe('unregister cleanup presentation', () => {
  afterEach(() => closeErrorDetails())

  it('does not repeat unregister cleanup failures in the retained-resource section', async () => {
    const wrapper = mountDialog()
    openErrorDetails({
      title: 'Deregistration failed',
      summary: 'Deregistration failed',
      taskType: 'source_unregister',
      reasons: ['NAS mount could not be removed'],
      cleanupResidue: {
        hasResidue: true,
        failures: ['NAS mount could not be removed'],
        retainedResources: ['source_nas_mount:32'],
      },
    })
    await nextTick()
    expect(wrapper.text()).toContain('NAS mount could not be removed')
    expect(wrapper.findAll('li').filter(item => item.text() === 'NAS mount could not be removed')).toHaveLength(1)
    expect(wrapper.text()).toContain('NAS mount (source ID: 32)')
    wrapper.unmount()
  })

  it('keeps unique legacy cleanup evidence in reasons rather than dropping it', async () => {
    const wrapper = mountDialog()
    openErrorDetails({
      title: 'Failed', summary: 'Failed', taskType: 'source_unregister',
      reasons: ['Original reason'],
      cleanupResidue: { hasResidue: true, failures: ['Different cleanup failure'] },
    })
    await flushPromises()
    const sections = wrapper.findAll('section')
    expect(sections).toHaveLength(1)
    expect(sections[0].text()).toContain('Original reason')
    expect(sections[0].text()).toContain('Different cleanup failure')
    wrapper.unmount()
  })

  it('does not add an unscoped duplicate of a source-scoped cleanup reason', async () => {
    const wrapper = mountDialog()
    openErrorDetails({
      title: 'Failed', summary: 'Failed', taskType: 'source_unregister',
      reasons: ['NAS A: Unmount failed'],
      entities: [{ id: 'nas:32', name: 'NAS A', type: 'source' }],
      cleanupResidue: { hasResidue: true, failures: ['Unmount failed'] },
    })
    await flushPromises()
    expect(wrapper.findAll('.hfl-error-details__list li').map(item => item.text())).toEqual(['NAS A: Unmount failed'])
    wrapper.unmount()
  })

  it('does not change cleanup presentation for other task types', async () => {
    const wrapper = mountDialog()
    openErrorDetails({
      title: 'Failed', summary: 'Failed', taskType: 'repository_operation',
      reasons: ['Reason'],
      cleanupResidue: { hasResidue: true, failures: ['Cleanup failure'] },
    })
    await flushPromises()
    expect(wrapper.findAll('section')).toHaveLength(2)
    expect(wrapper.text()).toContain('Cleanup failure')
    wrapper.unmount()
  })
})
