// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import type { LensSessionLink } from '../../../lib/lensApi'
import { en } from '../../../locales/en'
import chinese from '../../../../../../language-packs/packs/zh-hans/frontend/messages.json'
import spanish from '../../../../../../language-packs/packs/es/frontend/messages.json'
import CopilotLifecycleState from './CopilotLifecycleState.vue'

const ButtonStub = {
  props: ['type'],
  template: '<button :data-type="type"><slot /></button>',
}

function session(overrides: Partial<LensSessionLink> = {}): LensSessionLink {
  return {
    id: 1,
    title: 'Quarterly reports',
    knowledge_source: null,
    knowledge_source_name: null,
    sl_session_uuid: null,
    sl_assistant_uuid: null,
    agent_model_ref: 'model-ref',
    backup_config_id: 7,
    backup_source_name: 'Documents',
    backup_source_snapshot_id: 17,
    snapshot_created_at: '2026-08-12T03:00:00Z',
    snapshot_size_bytes: 4096,
    source_scopes_json: [{ backup_snapshot_directory_id: 31, source_path: '/reports' }],
    gateway_link: 11,
    gateway_selection_mode: 'auto',
    gateway_name: 'public-dg-01',
    gateway_scope: 'platform',
    status: 'active',
    lifecycle_status: 'provisioning',
    provision_phase: 'converting',
    provision_detail: 'Prepared 11 files for conversion.',
    document_conversion: {
      status: 'STARTED',
      phase: 'running',
      progress_message: 'Prepared 11 files for conversion.',
      counts: {
        total: 11,
        candidates: 6,
        success: 0,
        failed: 0,
        skipped: 0,
        unsupported: 5,
        unchanged: 0,
      },
      items: Array.from({ length: 5 }, (_, index) => ({
        name: `unsupported-${index + 1}.txt`,
        reason: 'UNSUPPORTED_TYPE',
        reason_label: 'Unsupported file type',
      })),
      warnings: [],
      usable: false,
    },
    last_message_at: null,
    last_assistant_message_at: null,
    last_viewed_at: null,
    has_unread: false,
    created_at: '2026-08-12T03:00:00Z',
    updated_at: '2026-08-12T03:00:00Z',
    ...overrides,
  }
}

function mountState(value: LensSessionLink, locale = 'en') {
  const i18n = createI18n({
    legacy: false,
    locale,
    messages: { en, 'zh-hans': chinese, es: spanish },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotLifecycleState, {
    props: { session: value },
    global: {
      plugins: [i18n],
      stubs: {
        ElButton: ButtonStub,
      },
    },
  })
}

describe('CopilotLifecycleState', () => {
  it('shows one conversion message and keeps file-level problems collapsed', () => {
    const wrapper = mountState(session())

    expect(wrapper.findAll('.copilot-conversion__detail')).toHaveLength(1)
    expect(wrapper.text().match(/Prepared 11 files for conversion\./g)).toHaveLength(1)
    expect(wrapper.get('summary').text()).toContain('View skipped files')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.get('.copilot-lifecycle-card').classes()).toContain('copilot-lifecycle-card--preparing')
    expect(wrapper.findAll('.copilot-lifecycle-steps > li')).toHaveLength(4)
    expect(wrapper.findAll('.copilot-lifecycle-steps > li')[2].find('.copilot-conversion').exists()).toBe(true)
  })

  it('preserves the heading and footer and uses four single-column steps', () => {
    const wrapper = mountState(session({ document_conversion: null }))
    expect(wrapper.get('h2').text()).toBe(en.insight.copilot.preparingYourChat)
    expect(wrapper.get('.copilot-lifecycle-heading p').text()).toBe(en.insight.copilot.selectedDataPreparing)
    expect(wrapper.get('small').text()).toBe(en.insight.copilot.backgroundPreparationHint)
    expect(wrapper.findAll('.copilot-lifecycle-step__heading').map((step) => step.text())).toEqual([
      'Validating Selected Data', 'Restoring Selected Data',
      'Converting Documents', 'Getting AI Copilot Ready',
    ])
    expect(wrapper.find('summary').exists()).toBe(false)
    const code = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotLifecycleState.vue'), 'utf8')
    expect(code).not.toContain('has-conversion')
    expect(code).not.toContain('border-left:')
    expect(code).not.toContain('min(720px')
  })

  function restoring(overrides = {}): LensSessionLink {
    return session({
      provision_phase: 'restoring', document_conversion: null,
      preparation_progress: {
        reused_data: false,
        restore: {
          status: 'running', phase: 'transferring', progress_percent: 62,
          bytes_done: 620 * 1024 * 1024, bytes_total: 1000 * 1024 * 1024,
          eta_seconds: 40, ...overrides,
        },
      },
    })
  }

  it('shows measured restore progress, bytes, and a conditional ETA under the active step', () => {
    const wrapper = mountState(restoring())
    const active = wrapper.get('.copilot-lifecycle-steps li.is-active')
    expect(active.text()).toContain('Restoring Selected Data')
    expect(active.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('62')
    expect(active.text()).toContain('Restored 620 MB / 1000 MB')
    expect(active.text()).toContain('Estimated remaining: about 40 seconds')
    expect(wrapper.findAll('[role="progressbar"]')).toHaveLength(1)
    expect(active.attributes('aria-current')).toBe('step')
  })

  it('requires explicit successful completion instead of treating 100% as a completed step', () => {
    const wrapper = mountState(restoring({ progress_percent: 100, eta_seconds: null }))
    expect(wrapper.findAll('.copilot-lifecycle-steps li')[1].classes()).toContain('is-active')
    expect(wrapper.findAll('.copilot-lifecycle-steps li')[2].classes()).toContain('is-pending')
  })

  it.each(['running', 'succeeded', 'failed'])('hides cached %s conversion details while restoring is still the current stage', (phase) => {
    const row = restoring()
    row.document_conversion = { ...session().document_conversion!, phase }
    const wrapper = mountState(row)
    const steps = wrapper.findAll('.copilot-lifecycle-steps > li')
    expect(steps[1].classes()).toContain('is-active')
    expect(steps[2].classes()).toContain('is-pending')
    expect(steps[2].get('.copilot-lifecycle-step__heading').text()).toBe('Converting Documents')
    expect(wrapper.find('.copilot-conversion').exists()).toBe(false)
    expect(wrapper.find('summary').exists()).toBe(false)
    expect(wrapper.find('.copilot-conversion__result').exists()).toBe(false)
  })

  it('finishes a confirmed successful restore without starting conversion before its stage arrives', async () => {
    const row = restoring({ status: 'success', phase: 'done', progress_percent: 100, eta_seconds: null })
    row.document_conversion = session().document_conversion
    const wrapper = mountState(row)
    let steps = wrapper.findAll('.copilot-lifecycle-steps > li')
    expect(steps[1].classes()).toContain('is-done')
    expect(steps[2].classes()).toContain('is-pending')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.find('.copilot-conversion').exists()).toBe(false)
    await wrapper.setProps({ session: { ...row, provision_phase: 'converting' } })
    steps = wrapper.findAll('.copilot-lifecycle-steps > li')
    expect(steps[2].classes()).toContain('is-active')
    expect(wrapper.find('.copilot-conversion').exists()).toBe(true)
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
  })

  it('unmounts and resets conversion details if preparation returns to restoring', async () => {
    const wrapper = mountState(session())
    wrapper.get('details').element.setAttribute('open', '')
    await wrapper.setProps({ session: { ...session(), provision_phase: 'restoring' } })
    expect(wrapper.find('details').exists()).toBe(false)
    await wrapper.setProps({ session: session() })
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
  })

  it.each([
    { progress_percent: null, bytes_total: null, eta_seconds: null },
    { status: 'pending', phase: 'queued', progress_percent: 0, eta_seconds: 40 },
    { phase: 'estimating', progress_percent: 10, eta_seconds: 40 },
    { progress_percent: Number.NaN, eta_seconds: 40 },
  ])('does not invent percentage or ETA for unknown or queued restore metrics: %s', (overrides) => {
    const wrapper = mountState(restoring(overrides))
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Estimated remaining')
  })

  it('hides expired ETA and explains restore finalization', () => {
    const wrapper = mountState(restoring({ phase: 'finalizing', eta_seconds: 40 }))
    expect(wrapper.text()).toContain('Finalizing restored data.')
    expect(wrapper.text()).not.toContain('Estimated remaining')
  })

  function finishedConversion(overrides: Partial<NonNullable<LensSessionLink['document_conversion']>> = {}) {
    return session({
      provision_phase: 'creating_assistant',
      preparation_progress: {
        reused_data: false,
        restore: {
          status: 'success', phase: 'done', progress_percent: 100,
          bytes_done: 1024 ** 3, bytes_total: 1024 ** 3, eta_seconds: null,
        },
      },
      document_conversion: {
        ...session().document_conversion!,
        status: 'SUCCESS', phase: 'succeeded', usable: true, progress_percent: 99,
        counts: { total: 13, candidates: 12, success: 12, failed: 0, skipped: 0, unsupported: 1, unchanged: 0 },
        items: [{
          name: '25519146543002846672.ofd',
          reason: 'UNSUPPORTED_TYPE', reason_label: 'Unsupported file type', outcome: 'skipped',
        }],
        warnings: [],
        ...overrides,
      },
    })
  }

  it('preserves verified data and actual restored size after advancing to later steps', () => {
    const wrapper = mountState(finishedConversion())
    expect(wrapper.text()).toContain('Selected data verified.')
    expect(wrapper.text()).toContain('Restored 1.00 GB.')
    expect(wrapper.text()).toContain('Configuring the AI Copilot.')
    expect(wrapper.text()).not.toContain('Estimated remaining')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
  })

  it('does not invent a restored size when the completed metrics are unavailable', () => {
    const row = finishedConversion()
    row.preparation_progress!.restore = null
    const wrapper = mountState(row)
    expect(wrapper.text()).toContain('Selected data restored.')
    expect(wrapper.text()).not.toContain('Restored 0')
    expect(wrapper.text()).not.toContain('Restored 4')
  })

  it('retains completed conversion outcomes and actionable format-specific file details', async () => {
    const wrapper = mountState(finishedConversion())
    expect(wrapper.get('.copilot-conversion__result').text()).toBe('12 documents available, 1 file skipped')
    expect(wrapper.get('summary').text()).toBe('View skipped files')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    await wrapper.get('summary').trigger('click')
    expect(wrapper.text()).toContain('Unsupported OFD format; document content extraction was skipped.')
    expect(wrapper.text()).toContain('use the new snapshot')
    expect(wrapper.text()).not.toContain('legacy .doc')
    expect(wrapper.find('.copilot-conversion__file-name').element.tagName).toBe('SPAN')
  })

  it('finishes conversion immediately on confirmed success while Chat provisioning catches up', async () => {
    const row = session({
      document_conversion: {
        ...session().document_conversion!,
        progress_percent: 76,
        progress_counts: {
          total: 7, candidates: 6, processed: 6,
          converted: 5, failed: 0, skipped: 0, unsupported: 1,
        },
      },
    })
    const wrapper = mountState(row)
    expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('76')
    const finished = finishedConversion().document_conversion!
    await wrapper.setProps({
      session: {
        ...row, provision_detail: 'Managed workspace conversion completed.',
        document_conversion: {
          ...finished, progress_message: 'Managed workspace conversion completed.',
          progress_counts: row.document_conversion!.progress_counts,
        },
      },
    })
    const steps = wrapper.findAll('.copilot-lifecycle-steps > li')
    expect(steps[2].classes()).toContain('is-done')
    expect(steps[2].find('.copilot-lifecycle-spin').exists()).toBe(false)
    expect(steps[3].classes()).toContain('is-active')
    expect(wrapper.text()).toContain('Data is prepared. Waiting to continue Chat setup.')
    expect(wrapper.find('.copilot-conversion__counts').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Managed workspace conversion completed.')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.get('.copilot-conversion__result').text()).toContain('12 documents available')
  })

  it('does not repeat an upstream ordinal-based message beside aggregate counts', () => {
    const wrapper = mountState(session({
      document_conversion: {
        ...session().document_conversion!,
        progress_message: 'Processed 3/6 convertible files.',
        progress_percent: 76,
        progress_counts: {
          total: 7, candidates: 6, processed: 6,
          converted: 5, failed: 0, skipped: 0, unsupported: 1,
        },
      },
    }))
    expect(wrapper.text()).toContain('Processed 5 / 6 documents')
    expect(wrapper.text()).not.toContain('Processed 3/6 convertible files.')
  })

  it.each([
    ['waiting', 'Data is prepared. Waiting to continue Chat setup.'],
    ['configuring', 'Configuring the AI Copilot.'],
    ['opening_session', 'Completing access authorization and opening the Chat.'],
    ['retrying', 'The service is temporarily unavailable. Retrying automatically.'],
  ] as const)('explains the actual final-stage state without a fabricated percentage: %s', (state, label) => {
    const row = finishedConversion()
    row.preparation_progress!.assistant_state = state
    const wrapper = mountState(row)
    const active = wrapper.get('.copilot-lifecycle-steps > li.is-active')
    expect(active.text()).toContain(label)
    expect(active.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Estimated remaining')
  })

  it('does not mark an all-unsupported conversion as successful usable content', () => {
    const wrapper = mountState(finishedConversion({
      usable: false,
      counts: { total: 1, candidates: 0, success: 0, failed: 0, skipped: 0, unsupported: 1, unchanged: 0 },
    }))
    const step = wrapper.findAll('.copilot-lifecycle-steps > li')[2]
    expect(step.classes()).toContain('is-warning')
    expect(step.classes()).not.toContain('is-done')
    expect(step.get('.copilot-lifecycle-step__heading').text()).toBe('Document processing finished')
    expect(step.text()).toContain('No usable document content was extracted, 1 file skipped')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
  })

  it('separates failed files from skipped files and does not count warnings as files', () => {
    const base = finishedConversion().document_conversion!
    const wrapper = mountState(finishedConversion({
      counts: { ...base.counts, total: 14, failed: 1 },
      items: [...base.items, { name: 'broken.pdf', reason: 'CORRUPT', reason_label: 'Corrupt', outcome: 'failed' }],
      warnings: [{ code: 'VISUAL_MODEL_NOT_CONFIGURED', label: 'Visual understanding is unavailable' }],
    }))
    expect(wrapper.get('.copilot-conversion__result').text()).toBe(
      '12 documents available, 1 file skipped, 1 file could not be converted',
    )
    expect(wrapper.get('summary').text()).toBe('View skipped and failed files')
    expect(wrapper.text()).not.toContain('items need attention')
    expect(wrapper.get('.is-warnings').text()).toContain('Visual')
  })

  it('treats unchanged cached documents as available instead of skipped', () => {
    const wrapper = mountState(finishedConversion({
      counts: { total: 12, candidates: 12, success: 10, failed: 0, skipped: 2, unsupported: 0, unchanged: 2 },
      items: [], warnings: [],
    }))
    expect(wrapper.get('.copilot-conversion__result').text()).toBe('12 documents available')
    expect(wrapper.find('summary').exists()).toBe(false)
  })

  it('does not treat truncated cached-document details as exact skipped counts', () => {
    const wrapper = mountState(finishedConversion({
      counts: { total: 120, candidates: 119, success: 10, failed: 0, skipped: 109, unsupported: 1, unchanged: 2 },
      items_truncated: 100,
    }))
    expect(wrapper.get('.copilot-conversion__result').text()).toBe(
      'At least 12 documents available, At least 1 file skipped',
    )
    expect(wrapper.text()).not.toContain('108 files skipped')
  })

  it('keeps active conversion in progress when numeric metrics are not available', () => {
    const wrapper = mountState(session({
      document_conversion: { ...session().document_conversion!, progress_percent: null },
    }))
    expect(wrapper.findAll('.copilot-lifecycle-steps > li')[2].classes()).toContain('is-active')
    expect(wrapper.find('.copilot-conversion__result').exists()).toBe(false)
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
  })

  it('shows conversion percent and actual processing counts, not successful document counts', () => {
    const conversion = session().document_conversion!
    const wrapper = mountState(session({
      document_conversion: {
        ...conversion,
        progress_percent: 46,
        progress_counts: {
          total: 45, candidates: 40, processed: 23, unsupported: 5,
          converted: 2, failed: 1, skipped: 0,
        },
      },
    }))
    const active = wrapper.get('.copilot-lifecycle-steps li.is-active')
    expect(active.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('46')
    expect(active.text()).toContain('Processed 18 / 40 documents')
    expect(active.text()).not.toContain('Estimated remaining')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.text()).not.toContain('Document preparation')
  })

  it('shows queued conversion without a fabricated percent or permanent format hint', () => {
    const wrapper = mountState(session({
      document_conversion: {
        ...session().document_conversion!,
        status: 'PENDING', progress_percent: 0,
        items: [], warnings: [], counts: { total: 0, candidates: 0, success: 0, failed: 0, skipped: 0, unsupported: 0, unchanged: 0 },
      },
    }))
    expect(wrapper.text()).toContain('Waiting for document conversion to start.')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.find('summary').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Supported document formats')
  })

  it('marks shared prepared data as already prepared without replaying restore or conversion', () => {
    const wrapper = mountState(session({
      provision_phase: 'creating_session',
      preparation_progress: { reused_data: true, restore: null },
    }))
    expect(wrapper.findAll('li.is-skipped')).toHaveLength(2)
    expect(wrapper.findAll('li.is-skipped').every((step) => step.text().includes('Already Prepared'))).toBe(true)
    expect(wrapper.get('li.is-active .copilot-lifecycle-step__heading').text()).toBe('Getting AI Copilot Ready')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.find('.copilot-conversion').exists()).toBe(false)
  })

  it('maps asynchronous scope resolution to the first preparation step', () => {
    const wrapper = mountState(session({
      provision_phase: 'resolving_scope',
      provision_detail: 'Checking selected files and folders.',
      document_conversion: null,
    }))

    const steps = wrapper.findAll('.copilot-lifecycle-steps li')
    expect(steps[0].classes()).toContain('is-active')
    expect(steps[1].classes()).toContain('is-pending')
  })

  it('shows a queue status without exposing internal position details', () => {
    const wrapper = mountState(session({
      provision_phase: 'queued',
      provision_detail: 'Waiting for Data Gateway.',
      queue_position: 2,
      queue_ahead: 3,
      document_conversion: null,
    }))

    expect(wrapper.text()).toContain(en.insight.copilot.gatewayQueueHint)
    expect(wrapper.text()).not.toContain('3 Chat(s) are ahead')
    expect(wrapper.text()).not.toContain('Data Gateway')
    expect(wrapper.text()).not.toContain('public-dg-01')
    expect(wrapper.get('[role="status"]').attributes('aria-live')).toBe('polite')
  })

  it.each(['en', 'zh-hans', 'es'])('keeps queued preparation in one paragraph and background guidance only in the footer: %s', (locale) => {
    const copy = (locale === 'zh-hans' ? chinese : locale === 'es' ? spanish : en).insight.copilot
    const wrapper = mountState(session({
      provision_phase: 'queued', queue_position: 2, queue_ahead: 3,
      document_conversion: null,
    }), locale)
    const detail = wrapper.get('li.is-active .copilot-lifecycle-step__detail')
    expect(detail.findAll('p')).toHaveLength(1)
    expect(detail.text()).toBe(copy.gatewayQueueHint)
    expect(detail.element.textContent).not.toContain('\n')
    expect(detail.findAll('p').map(paragraph => paragraph.text())).not.toContain(copy.gatewayQueueTitle)
    expect(wrapper.get('small').text()).toBe(copy.backgroundPreparationHint)
    expect(wrapper.text().split(copy.backgroundPreparationHint)).toHaveLength(2)
    wrapper.unmount()
  })

  it('shows automatic compensation as recovery instead of chat deletion', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      cleanup_intent: 'reset_for_retry',
      cleanup_status: 'running',
    }))

    expect(wrapper.text()).toContain('Preparing Chat for Retry')
    expect(wrapper.text()).not.toContain('Deleting Chat')
    expect(wrapper.text()).not.toContain('Try Again')
  })

  it('explains retained Chat data when a failed conversion can be retried', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      cleanup_intent: 'none',
      cleanup_status: 'none',
      knowledge_source: 7,
      document_conversion: {
        ...session().document_conversion!,
        status: 'FAILURE',
        phase: 'failed',
        error: 'DATASOURCE_CONVERSION_REBIND_PAUSED',
      },
    }))

    expect(wrapper.text()).toContain('Automatic conversion recovery paused')
    expect(wrapper.text()).toContain('Try Again')
    expect(wrapper.text()).toContain('Delete Chat')
  })

  it('explains retained resources after a non-conversion preparation failure', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      cleanup_intent: 'none',
      cleanup_status: 'none',
      knowledge_source: 7,
      document_conversion: null,
    }))

    expect(wrapper.text()).toContain('Existing Chat data is preserved')
  })

  it('explains blocked cleanup without an endless deleting spinner', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      cleanup_intent: 'reset_for_retry',
      cleanup_status: 'blocked',
    }))

    expect(wrapper.text()).toContain('Chat Cleanup Paused')
    expect(wrapper.text()).toContain('Chat cleanup failed')
    expect(wrapper.text()).not.toContain('Deleting Chat')
  })

  it('offers a bounded delete retry when deletion cleanup is blocked', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'deleting',
      cleanup_intent: 'delete_session',
      cleanup_status: 'blocked',
    }))

    expect(wrapper.text()).toContain('Chat Couldn’t Be Deleted')
    expect(wrapper.text()).toContain('Retry Delete')
    expect(wrapper.text()).not.toContain('Force Delete')
    expect(wrapper.text()).not.toContain('Deleting Chat')
  })

  it('offers force cleanup for an eligible private Gateway cleanup', async () => {
    const wrapper = mountState(session({
      lifecycle_status: 'deleting',
      cleanup_intent: 'delete_session',
      cleanup_status: 'blocked',
      gateway_scope: 'organization',
      force_delete_available: true,
    }))

    expect(wrapper.text()).toContain('Force Cleanup will remove this Chat')
    expect(wrapper.text()).toContain('Force Delete')
    await wrapper.findAll('.copilot-lifecycle-actions button')[1]!.trigger('click')
    expect(wrapper.emitted('forceDelete')).toHaveLength(1)
  })

  it('offers force cleanup while an eligible private Gateway cleanup is pending', async () => {
    const wrapper = mountState(session({
      lifecycle_status: 'deleting',
      cleanup_intent: 'delete_session',
      cleanup_status: 'pending',
      gateway_scope: 'organization',
      force_delete_available: true,
    }))

    expect(wrapper.text()).toContain('Deleting Chat')
    expect(wrapper.text()).toContain('Force Cleanup will remove this Chat')
    expect(wrapper.text()).toContain('Force Delete')
    await wrapper.get('.copilot-lifecycle-actions button').trigger('click')
    expect(wrapper.emitted('forceDelete')).toHaveLength(1)
  })

  it('shows a safe lifecycle error and hides retry for configuration failures', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      lifecycle_error_code: 'INSIGHT.CHAT_MODEL_NOT_VISION_CAPABLE',
      lifecycle_error_message: 'The configured AI model is not compatible with this Chat.',
      lifecycle_error_retryable: false,
    }))

    expect(wrapper.text()).toContain('The configured AI model is not compatible with this Chat.')
    expect(wrapper.text()).not.toContain('Try Again')
  })

  it('resolves established lifecycle error codes through the product registry', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      lifecycle_error_code: 'INSIGHT.DATA_GATEWAY_UNAVAILABLE',
      lifecycle_error_message: 'internal gateway diagnostic',
      lifecycle_error_retryable: true,
    }))

    expect(wrapper.text()).toContain('Bring its Agent and LensNode online')
    expect(wrapper.text()).not.toContain('internal gateway diagnostic')
    expect(wrapper.text()).toContain('Try Again')
  })

  it('uses the Protection button hierarchy for retryable preparation failures', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      lifecycle_error_code: 'INSIGHT.DATA_GATEWAY_UNAVAILABLE',
      lifecycle_error_retryable: true,
    }))
    const buttons = wrapper.findAll('.copilot-lifecycle-actions button')

    expect(buttons.map((button) => [button.text(), button.attributes('data-type')])).toEqual([
      ['Delete Chat', 'danger'],
      ['Try Again', 'primary'],
    ])
  })

  it('uses quota context for Public Data Gateway capacity failures', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      lifecycle_error_code: 'SUBSCRIPTION.QUOTA_EXCEEDED',
      lifecycle_error_message: 'internal quota diagnostic',
      lifecycle_error_retryable: true,
      lifecycle_error_meta: {
        quota_type: 'gateway.public_capacity_bytes',
        scope: 'gateway',
      },
    }))

    expect(wrapper.text()).toContain('shared Data Gateway currently has insufficient capacity')
    expect(wrapper.text()).not.toContain('internal quota diagnostic')
    expect(wrapper.text()).toContain('Try Again')
  })

  it('keeps retry available for legacy failures without a structured code', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      lifecycle_error_code: '',
      lifecycle_error_message: '',
      lifecycle_error_retryable: false,
    }))

    expect(wrapper.text()).toContain('Something went wrong while preparing')
    expect(wrapper.text()).toContain('Try Again')
  })
})
