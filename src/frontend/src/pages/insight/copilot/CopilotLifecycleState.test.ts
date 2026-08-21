// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import type { LensSessionLink } from '../../../lib/lensApi'
import { en } from '../../../locales/en'
import CopilotLifecycleState from './CopilotLifecycleState.vue'

function session(overrides: Partial<LensSessionLink> = {}): LensSessionLink {
  return {
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

function mountState(value: LensSessionLink) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotLifecycleState, {
    props: { session: value },
    global: {
      plugins: [i18n],
      stubs: {
        ElButton: { template: '<button><slot /></button>' },
      },
    },
  })
}

describe('CopilotLifecycleState', () => {
  it('shows one conversion message and keeps file-level problems collapsed', () => {
    const wrapper = mountState(session())

    expect(wrapper.findAll('.copilot-conversion__detail')).toHaveLength(1)
    expect(wrapper.text().match(/Prepared 11 files for conversion\./g)).toHaveLength(1)
    expect(wrapper.get('summary').text()).toContain('5 items need attention')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.get('.copilot-lifecycle-card').classes()).toContain('has-conversion')
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

  it('shows automatic compensation as recovery instead of chat deletion', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      cleanup_intent: 'reset_for_retry',
      cleanup_status: 'running',
    }))

    expect(wrapper.text()).toContain('Recovering Chat Resources')
    expect(wrapper.text()).not.toContain('Deleting Chat')
    expect(wrapper.text()).not.toContain('Try Again')
  })

  it('explains blocked cleanup without an endless deleting spinner', () => {
    const wrapper = mountState(session({
      lifecycle_status: 'failed',
      cleanup_intent: 'reset_for_retry',
      cleanup_status: 'blocked',
    }))

    expect(wrapper.text()).toContain('Chat Recovery Needs Attention')
    expect(wrapper.text()).toContain('Temporary data is being retained')
    expect(wrapper.text()).not.toContain('Deleting Chat')
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
