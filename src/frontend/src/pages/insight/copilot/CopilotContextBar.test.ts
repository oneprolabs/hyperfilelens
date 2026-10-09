// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { ElTag } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'
import chinese from '../../../../../../language-packs/packs/zh-hans/frontend/messages.json'
import spanish from '../../../../../../language-packs/packs/es/frontend/messages.json'
import type { LensSessionLink } from '../../../lib/lensApi'
import { formatLocalDateTime } from '../../../lib/dateTime'
import { en } from '../../../locales/en'
import CopilotContextBar from './CopilotContextBar.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const DialogStub = defineComponent({
  props: ['modelValue', 'title'],
  template: '<section v-if="modelValue" class="details"><h2>{{ title }}</h2><slot /></section>',
})

const selectedPath = `C:/${chinese.insight.kb.fieldBackupSource}`

function session(overrides: Partial<LensSessionLink> = {}): LensSessionLink {
  return {
    id: 7,
    title: 'zjbtestchat',
    backup_source_name: 'zjb',
    snapshot_created_at: '2026-10-09T09:30:00+08:00',
    created_at: '2026-10-09T09:49:00+08:00',
    source_scopes_json: [{
      source_path: selectedPath,
      backup_snapshot_directory_id: 31,
    }],
    gateway_name: 'zjb',
    gateway_scope: 'platform',
    gateway_selection_mode: 'auto',
    lifecycle_status: 'ready',
    multimodal_model_ref: 'model-ref',
    ...overrides,
  } as LensSessionLink
}

function mountBar(row = session(), locale = 'en') {
  return mount(CopilotContextBar, {
    props: { session: row },
    global: {
      plugins: [createI18n({
        legacy: false, locale, messages: { en, 'zh-hans': chinese, es: spanish },
        missingWarn: false, fallbackWarn: false,
      })],
      stubs: { ElDialog: DialogStub, ElButton: true },
    },
  })
}

describe('Copilot context summary', () => {
  it('shows only source, snapshot time, gateway, and Chat creation time in that order', () => {
    const row = session()
    const wrapper = mountBar(row)
    const summary = wrapper.get('.copilot-context-bar__summary')
    expect(summary.findAll('.copilot-context-bar__field').map((field) => field.text())).toEqual([
      'Backup Source:zjb',
      `Snapshot:${formatLocalDateTime(row.snapshot_created_at)}`,
      'Gateway:Public zjb',
      `Created ${formatLocalDateTime(row.created_at)}`,
    ])
    expect(summary.text()).not.toContain('Protected snapshot')
    expect(summary.text()).not.toContain(selectedPath)
    expect(summary.text()).not.toMatch(/[·|()/]/)
    expect(summary.get('time').attributes('datetime')).toBe(row.snapshot_created_at)
    expect(summary.get('.copilot-context-bar__gateway .copilot-context-bar__value').text()).toBe('Public zjb')
    expect(wrapper.get('h1').text()).toBe('zjbtestchat')
    expect(wrapper.get('.copilot-context-bar__status').text()).toBe('Ready')
    wrapper.unmount()
  })

  it.each([
    ['platform', 'manual', 'Public'],
    ['organization', 'auto', 'Private'],
    ['user', 'auto', 'Private'],
    [null, 'manual', 'Private'],
  ] as const)('uses persisted scope %s and mode %s for the gateway text', (scope, mode, label) => {
    const wrapper = mountBar(session({
      gateway_scope: scope,
      gateway_selection_mode: mode,
      gateway_name: 'office-gateway',
    }))
    const gateway = wrapper.get('.copilot-context-bar__gateway')
    expect(gateway.get('.copilot-context-bar__value').text()).toBe(`${label} office-gateway`)
    wrapper.unmount()
  })

  it('keeps the status tag but renders gateway type and name as one plain-text value', () => {
    const wrapper = mountBar()
    const tags = wrapper.findAllComponents(ElTag)
    expect(tags).toHaveLength(1)
    for (const tag of tags) {
      expect(tag.props('size')).toBe('small')
      expect(tag.props('effect')).toBe('light')
      expect(tag.props('round')).toBe(false)
    }
    expect(tags[0].props('type')).toBe('success')
    const gatewayValue = wrapper.get('.copilot-context-bar__gateway .copilot-context-bar__value')
    expect(gatewayValue.element.tagName).toBe('SPAN')
    expect(gatewayValue.text()).toBe('Public zjb')
    expect(gatewayValue.classes()).not.toContain('el-tag')
    expect(gatewayValue.attributes('role')).not.toBe('button')
    expect(wrapper.find('.copilot-context-bar__gateway-type').exists()).toBe(false)
    expect(wrapper.get('.copilot-context-bar__status').find('i').exists()).toBe(false)
    const code = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotContextBar.vue'), 'utf8')
    expect(code).not.toContain('copilot-status-pulse')
    expect(code).not.toContain('border-radius: 999px')
    expect(code).not.toContain('.copilot-context-bar__gateway-type')
    wrapper.unmount()
  })

  it.each([
    [{ lifecycle_status: 'ready' }, 'Ready', 'success'],
    [{ lifecycle_status: 'provisioning' }, 'Preparing chat…', 'info'],
    [{ lifecycle_status: 'deleting' }, 'Deleting…', 'info'],
    [{ active_run_status: 'queued' }, 'Answering…', 'info'],
    [{ active_run_status: 'running' }, 'Answering…', 'info'],
    [{ active_run_status: 'streaming' }, 'Answering…', 'info'],
    [{ lifecycle_status: 'failed', active_run_status: 'running' }, 'Preparation failed', 'danger'],
    [{ lifecycle_status: 'failed', cleanup_intent: 'reset_for_retry', cleanup_status: 'running' }, 'Recovering chat…', 'info'],
    [{ lifecycle_status: 'failed', cleanup_intent: 'reset_for_retry', cleanup_status: 'blocked' }, 'Recovery needs attention', 'warning'],
  ] as const)('keeps %s mapped to the shared %s status tag', (overrides, label, tone) => {
    const wrapper = mountBar(session(overrides))
    const tag = wrapper.findAllComponents(ElTag)[0]
    expect(tag.text()).toBe(label)
    expect(tag.props('type')).toBe(tone)
    wrapper.unmount()
  })

  it('does not invent a gateway name or use Chat creation time as a missing snapshot', () => {
    const wrapper = mountBar(session({ gateway_name: ' ', snapshot_created_at: null }))
    expect(wrapper.get('.copilot-context-bar__snapshot').text()).toBe('Snapshot:—')
    expect(wrapper.get('.copilot-context-bar__snapshot').find('time').exists()).toBe(false)
    expect(wrapper.get('.copilot-context-bar__gateway').text()).toBe('Gateway:Public')
    expect(wrapper.get('.copilot-context-bar__gateway .copilot-context-bar__value').text()).toBe('Public')
    wrapper.unmount()
  })

  it('reacts to a new applied snapshot without changing Chat creation time', async () => {
    const wrapper = mountBar()
    const created = wrapper.get('.copilot-context-bar__created').text()
    const updated = session({ snapshot_created_at: '2026-10-09T10:30:00+08:00' })
    await wrapper.setProps({ session: updated })
    expect(wrapper.get('.copilot-context-bar__snapshot time').text()).toBe(formatLocalDateTime(updated.snapshot_created_at))
    expect(wrapper.get('.copilot-context-bar__created').text()).toBe(created)
    wrapper.unmount()
  })

  it('always includes year and seconds and handles invalid timestamps', () => {
    const timestamp = '2025-10-09T09:30:00+08:00'
    const wrapper = mountBar(session({ snapshot_created_at: timestamp, created_at: 'invalid' }))
    expect(wrapper.get('.copilot-context-bar__snapshot').text()).toBe(`Snapshot:${formatLocalDateTime(timestamp)}`)
    expect(wrapper.get('.copilot-context-bar__created').text()).toBe('Created —')
    wrapper.unmount()
  })

  it.each([
    [
      'zh-hans',
      `${chinese.insight.kb.fieldBackupSource}:`,
      `${chinese.insight.copilot.contextSnapshotLabel}:`,
      `${chinese.insight.copilot.contextGatewayLabel}:${chinese.insight.copilot.gatewayTypePrivate} office-gateway`,
      chinese.insight.copilot.contextCreatedAt.replace('{time}', '').trim(),
    ],
    ['es', 'Origen de copia de seguridad:', 'Instantánea:', 'Puerta de enlace:Privado office-gateway', 'Creado:'],
  ])('localizes the metadata labels and type in %s', (locale, source, snapshot, gateway, created) => {
    const wrapper = mountBar(session({ gateway_scope: 'organization', gateway_name: 'office-gateway' }), locale)
    expect(wrapper.get('.copilot-context-bar__source').text()).toBe(`${source}zjb`)
    expect(wrapper.get('.copilot-context-bar__snapshot').text()).toBe(`${snapshot}${formatLocalDateTime(session().snapshot_created_at)}`)
    expect(wrapper.get('.copilot-context-bar__gateway').text()).toBe(gateway)
    expect(wrapper.get('.copilot-context-bar__created').text()).toContain(created)
    wrapper.unmount()
  })

  it.each(['en', 'zh-hans', 'es'])('uses the same numeric timestamp format in %s', (locale) => {
    const wrapper = mountBar(session({
      snapshot_created_at: '2026-09-30T16:55:43',
      created_at: '2026-10-09T09:49:12',
    }), locale)
    expect(wrapper.get('.copilot-context-bar__snapshot time').text()).toBe('2026-09-30 16:55:43')
    expect(wrapper.get('.copilot-context-bar__created').text()).toContain('2026-10-09 09:49:12')
    wrapper.unmount()
  })

  it.each(['source', 'snapshot'])('renders %s as non-interactive metadata', async (field) => {
    const wrapper = mountBar()
    const value = wrapper.get(`.copilot-context-bar__${field}`)
    expect(value.element.tagName).toBe('SPAN')
    expect(value.find('button').exists()).toBe(false)
    expect(value.attributes('role')).not.toBe('button')
    expect(value.attributes('tabindex')).toBeUndefined()
    await value.trigger('click')
    expect(wrapper.find('.details').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(['ready', 'provisioning', 'failed', 'deleting'])('opens existing details only from the native title button in %s', async (state) => {
    const wrapper = mountBar(session({ lifecycle_status: state }))
    const title = wrapper.get('h1 button')
    expect(title.element.tagName).toBe('BUTTON')
    expect(title.attributes('type')).toBe('button')
    expect(title.attributes('disabled')).toBeUndefined()
    expect(title.attributes('aria-haspopup')).toBe('dialog')
    expect(title.attributes('aria-expanded')).toBe('false')
    expect(title.attributes('aria-label')).toContain('Chat Details')
    await wrapper.get('.copilot-context-bar__status').trigger('click')
    expect(wrapper.find('.details').exists()).toBe(false)
    await title.trigger('click')
    expect(title.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.details').text()).toContain(selectedPath)
    expect(wrapper.get('.details').classes()).toContain('hfl-flow-action-dialog')
    wrapper.unmount()
  })

  it('removes origin, navigation actions and processing note while preserving snapshot and gateway information', async () => {
    const wrapper = mountBar(session({
      snapshot_size_bytes: 754 * 1024,
      data_context: {
        origin: 'protected_snapshot', origin_label: 'Protected snapshot',
        backup_config_id: 1, backup_source_snapshot_id: 2,
        snapshot_created_at: session().snapshot_created_at,
        processing_location: 'public_gateway', processing_location_label: 'Processing on gateway',
        gateway_name: 'actual-gateway', restore_path: '/restore-records/2', backup_detail_path: '/backup-configs/1',
      },
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    const details = wrapper.get('.details')
    expect(details.text()).toContain('754 KB')
    expect(details.text()).toContain('actual-gateway')
    expect(details.text()).toContain('Data Gateway')
    expect(details.text()).toContain('Gateway Name')
    expect(details.text()).not.toContain('Protected snapshot')
    expect(details.text()).not.toContain('Processing Location')
    expect(details.text()).not.toContain(en.insight.copilot.processingLocationNote)
    expect(details.text()).not.toContain(en.insight.copilot.openSnapshotRestore)
    expect(details.text()).not.toContain(en.insight.copilot.openBackupDetail)
    expect(details.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders saved folder, file and unknown-path types without guessing from extensions', async () => {
    const wrapper = mountBar(session({
      source_scopes_json: [
        { backup_snapshot_directory_id: 1, source_path: 'C:\\project\\docs\\', path_type: 'dir' },
        { backup_snapshot_directory_id: 2, source_path: '/project/quality_report.py', path_type: 'file' },
        { backup_snapshot_directory_id: 3, source_path: '/project/legacy.pdf', path_type: 'unknown' },
      ],
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    const rows = wrapper.findAll('.copilot-details__scope')
    expect(wrapper.find('.copilot-details__scope-name').exists()).toBe(false)
    expect(rows.map(row => row.get('.copilot-details__scope-path').text())).toEqual([
      'C:\\project\\docs\\', '/project/quality_report.py', '/project/legacy.pdf',
    ])
    expect(rows.map(row => row.get('[role="img"]').attributes('aria-label'))).toEqual([
      'Folder', 'File', 'Path',
    ])
    wrapper.unmount()
  })

  it('shows unrecorded scope instead of claiming the whole backup was selected', async () => {
    const wrapper = mountBar(session({ source_scopes_json: [] }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.get('.details').text()).toContain('Selection scope was not recorded.')
    expect(wrapper.find('.copilot-details__scope').exists()).toBe(false)
    wrapper.unmount()
  })

  it('preserves long and root paths and updates to the active Chat selection', async () => {
    const longPath = `/project/${'nested-folder/'.repeat(20)}report.txt`
    const wrapper = mountBar(session({
      source_scopes_json: [
        { backup_snapshot_directory_id: 1, source_path: '/', path_type: 'dir' },
        { backup_snapshot_directory_id: 2, source_path: longPath, path_type: 'file' },
      ],
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.copilot-details__scope-path')[1].text()).toBe(longPath)
    expect(wrapper.findAll('.copilot-details__scope-path')[0].text()).toBe('/')
    await wrapper.setProps({ session: session({
      source_scopes_json: [{ backup_snapshot_directory_id: 3, source_path: '/other/new.txt', path_type: 'file' }],
    }) })
    expect(wrapper.findAll('.copilot-details__scope')).toHaveLength(1)
    expect(wrapper.get('.copilot-details__scope-path').text()).toBe('/other/new.txt')
    wrapper.unmount()
  })

  it('shows separate ready, failed and unsupported conversion rows without middle-dot separators', async () => {
    const wrapper = mountBar(session({
      document_conversion: {
        status: 'SUCCESS', phase: 'succeeded', usable: true,
        counts: { total: 9, candidates: 9, success: 4, unchanged: 2, failed: 2, unsupported: 1, skipped: 1 },
        items: [{ name: 'report.ofd', reason: 'UNSUPPORTED_TYPE', reason_label: 'Unsupported file type' }],
        warnings: [],
      },
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    const rows = wrapper.findAll('.copilot-details__conversion-count')
    expect(rows.map(row => row.get('dt').text())).toEqual(['Ready', 'Failed', 'Unsupported'])
    expect(rows.map(row => row.get('dd').text())).toEqual(['6 documents', '2 documents', '1 document'])
    expect(wrapper.get('.details').text()).toContain('report.ofd')
    expect(wrapper.get('.details').text()).not.toContain('·')
    wrapper.unmount()
  })

  it('uses the agreed Chinese dialog labels and keeps existing HFL dialog styling', async () => {
    const wrapper = mountBar(session(), 'zh-hans')
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.get('.details h2').text()).toBe(chinese.insight.copilot.chatDetailsTitle)
    expect(wrapper.get('.details').text()).toContain(chinese.insight.copilot.detailsProcessingLocation)
    expect(wrapper.get('.details').text()).toContain(chinese.insight.copilot.detailsGatewayName)
    expect(wrapper.get('.details').text()).not.toContain(chinese.insight.copilot.processingLocationNote)
    const code = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotContextBar.vue'), 'utf8')
    expect(code).toContain("import '../../../components/backupSourceFlowActionDialog.css'")
    expect(code).toContain("import '../../../styles/detail-page-ui.css'")
    expect(code).toContain('width="min(680px, calc(100vw - 32px))"')
    expect(code).toContain('max-width: calc(100vw - 32px)')
    wrapper.unmount()
  })

  it('uses shared detail sections without indicators and groups files with reasons in a plain list', async () => {
    const wrapper = mountBar(session({
      document_conversion: {
        status: 'SUCCESS', phase: 'succeeded', usable: true,
        counts: { total: 7, candidates: 6, success: 6, unchanged: 0, failed: 0, unsupported: 1, skipped: 0 },
        items: [{ name: '25519146543002846672.ofd', reason: 'UNSUPPORTED_TYPE', reason_label: 'Unsupported file type' }],
        warnings: [],
      },
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.hfl-detail-section.hfl-detail-card')).toHaveLength(4)
    expect(wrapper.find('.hfl-detail-card__indicator').exists()).toBe(false)
    expect(wrapper.get('.details').classes()).toContain('hfl-flow-action-dialog--form')
    const list = wrapper.get('.copilot-details__problems')
    expect(list.element.tagName).toBe('UL')
    const item = list.get('li')
    expect(item.get('.copilot-details__problem-file').text()).toBe('25519146543002846672.ofd')
    expect(item.get('.copilot-details__problem-reason').text()).toBe('Unsupported file type')
    expect(item.find('strong').exists()).toBe(false)
    expect(wrapper.find('.details table').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the existing settings action unchanged', async () => {
    const wrapper = mountBar()
    await wrapper.get('.copilot-context-bar__settings').trigger('click')
    expect(wrapper.emitted('edit-execution')).toHaveLength(1)
    wrapper.unmount()
  })

  function manyProblems(overrides: Partial<LensSessionLink> = {}): LensSessionLink {
    return session({
      document_conversion: {
        status: 'SUCCESS', phase: 'succeeded', usable: false,
        counts: { total: 16, candidates: 2, success: 0, unchanged: 0, failed: 2, unsupported: 14, skipped: 0 },
        items: Array.from({ length: 16 }, (_, index) => ({
          name: index < 14 ? `invoice-${index}.ofd` : `damaged-${index}.pdf`,
          reason: index < 14 ? 'UNSUPPORTED_TYPE' : 'CORRUPT',
          reason_label: index < 14 ? 'Unsupported file type' : 'File is corrupted or unreadable',
          outcome: index < 14 ? 'skipped' as const : 'failed' as const,
        })),
        warnings: [],
      },
      ...overrides,
    })
  }

  it('expands and collapses all returned problem files without replacing distinct reasons', async () => {
    const wrapper = mountBar(manyProblems())
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(12)
    const action = wrapper.get('.copilot-details__show-more')
    expect(action.text()).toBe('Show 4 more files')
    expect(action.attributes('aria-expanded')).toBe('false')
    expect(action.attributes('aria-controls')).toBe(wrapper.get('.copilot-details__problems').attributes('id'))
    expect(wrapper.find('.copilot-details__partial-problems').exists()).toBe(false)
    await action.trigger('click')
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(16)
    expect(wrapper.findAll('.copilot-details__problem-reason').slice(-2).map(reason => reason.text())).toEqual([
      en.insight.copilot.conversionReasons.CORRUPT,
      en.insight.copilot.conversionReasons.CORRUPT,
    ])
    expect(action.text()).toBe('Show fewer files')
    expect(action.attributes('aria-expanded')).toBe('true')
    await action.trigger('click')
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(12)
    wrapper.unmount()
  })

  it('resets expansion when closing details or switching Chats', async () => {
    const wrapper = mountBar(manyProblems())
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    await wrapper.get('.copilot-details__show-more').trigger('click')
    await wrapper.findComponent(DialogStub).vm.$emit('update:modelValue', false)
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(12)
    await wrapper.get('.copilot-details__show-more').trigger('click')
    await wrapper.setProps({ session: manyProblems({ id: 8 }) })
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(12)
    expect(wrapper.get('.copilot-details__show-more').attributes('aria-controls')).toBe('copilot-detail-problems-8')
    wrapper.unmount()
  })

  it('clearly marks source-truncated details and only expands entries actually returned', async () => {
    const row = manyProblems()
    row.document_conversion!.items_truncated = 100
    const wrapper = mountBar(row)
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.get('.copilot-details__partial-problems').text()).toBe(en.insight.copilot.detailsPartialProblemFiles)
    expect(wrapper.get('.copilot-details__show-more').text()).toBe('Show 4 more files')
    await wrapper.get('.copilot-details__show-more').trigger('click')
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(16)
    expect(wrapper.find('.copilot-details__partial-problems').exists()).toBe(true)
    wrapper.unmount()
  })

  it('marks missing issue entries even when the source omits its truncation flag', async () => {
    const row = manyProblems()
    row.document_conversion!.items = row.document_conversion!.items.slice(0, 3)
    const wrapper = mountBar(row)
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.copilot-details__problem-item')).toHaveLength(3)
    expect(wrapper.find('.copilot-details__show-more').exists()).toBe(false)
    expect(wrapper.get('.copilot-details__partial-problems').text()).toBe(en.insight.copilot.detailsPartialProblemFiles)
    wrapper.unmount()
  })

  it.each(['en', 'zh-hans', 'es'])('localizes expansion and partial-list warnings in %s', async (locale) => {
    const row = manyProblems()
    row.document_conversion!.items_truncated = 10
    const messages = locale === 'zh-hans' ? chinese : locale === 'es' ? spanish : en
    const wrapper = mountBar(row, locale)
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.get('.copilot-details__show-more').text()).toBe(
      messages.insight.copilot.detailsShowMoreProblemFiles.split('|').at(-1)!.trim().replace('{count}', '4'),
    )
    expect(wrapper.get('.copilot-details__partial-problems').text()).toBe(messages.insight.copilot.detailsPartialProblemFiles)
    wrapper.unmount()
  })

  it('keeps skipped conversions separate from cached and unsupported files', async () => {
    const wrapper = mountBar(session({
      document_conversion: {
        status: 'SUCCESS', phase: 'succeeded', usable: true,
        counts: { total: 6, candidates: 5, success: 2, unchanged: 1, failed: 0, unsupported: 1, skipped: 3 },
        items: [], warnings: [],
      },
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    const rows = wrapper.findAll('.copilot-details__conversion-count')
    expect(rows.map(row => row.get('dt').text())).toEqual(['Ready', 'Skipped', 'Unsupported'])
    expect(rows.map(row => row.get('dd').text())).toEqual(['3 documents', '2 documents', '1 document'])
    wrapper.unmount()
  })

  it('shows in-progress status even when an unsupported file has already been discovered', async () => {
    const wrapper = mountBar(session({
      document_conversion: {
        status: 'STARTED', phase: 'running', usable: false,
        counts: { total: 2, candidates: 1, success: 0, unchanged: 0, failed: 0, unsupported: 1, skipped: 0 },
        items: [{ name: 'report.ofd', reason: 'UNSUPPORTED_TYPE', reason_label: 'Unsupported file type' }],
        warnings: [],
      },
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.get('.details').text()).toContain(en.insight.copilot.documentConversionRunning)
    expect(wrapper.get('.details').text()).not.toContain(en.insight.copilot.documentConversionOk)
    wrapper.unmount()
  })

  it('does not classify truncated cached file details as exact skipped counts', async () => {
    const wrapper = mountBar(session({
      document_conversion: {
        status: 'SUCCESS', phase: 'succeeded', usable: true, items_truncated: 100,
        counts: { total: 120, candidates: 119, success: 10, unchanged: 2, failed: 0, unsupported: 1, skipped: 109 },
        items: [{ name: 'report.ofd', reason: 'UNSUPPORTED_TYPE', reason_label: 'Unsupported file type', outcome: 'skipped' }],
        warnings: [],
      },
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    const rows = wrapper.findAll('.copilot-details__conversion-count')
    expect(rows.map(row => row.get('dt').text())).toEqual(['Ready', 'Unsupported'])
    expect(rows.map(row => row.get('dd').text())).toEqual(['At least 12 documents', '1 document'])
    expect(wrapper.get('.details').text()).not.toContain('107 documents')
    wrapper.unmount()
  })

  it('preserves whitespace in saved file names and excludes blank historical selections', async () => {
    const path = '/reports/report with trailing space '
    const wrapper = mountBar(session({
      source_scopes_json: [
        { backup_snapshot_directory_id: 1, source_path: path, path_type: 'file' },
        { backup_snapshot_directory_id: 2, source_path: '  ', path_type: 'unknown' },
      ],
    }))
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.copilot-details__scope')).toHaveLength(1)
    expect(wrapper.get('.copilot-details__scope-path').element.textContent).toBe(path)
    wrapper.unmount()
  })

  it.each(['en', 'zh-hans', 'es'])('localizes detail sections and document counts in %s', async (locale) => {
    const messages = locale === 'zh-hans' ? chinese : locale === 'es' ? spanish : en
    const wrapper = mountBar(session({
      document_conversion: {
        status: 'SUCCESS', phase: 'succeeded', usable: true,
        counts: { total: 2, candidates: 2, success: 2, unchanged: 0, failed: 0, unsupported: 0, skipped: 0 },
        items: [], warnings: [],
      },
    }), locale)
    await wrapper.get('.copilot-context-bar__title').trigger('click')
    expect(wrapper.findAll('.details h3').map(heading => heading.text())).toEqual([
      messages.insight.copilot.detailsDataSource,
      messages.insight.copilot.detailsFilesFolders,
      messages.insight.copilot.detailsProcessingLocation,
      messages.insight.copilot.documentConversionTitle,
    ])
    expect(wrapper.get('.copilot-details__conversion-count dd').text()).toBe(
      messages.insight.copilot.detailsConversionCount.split('|').at(-1)!.trim().replace('{count}', '2'),
    )
    wrapper.unmount()
  })

  it('uses theme-aware CSS separator lines and preserves all fields on narrow screens', () => {
    const code = readFileSync(resolve(process.cwd(), 'src/pages/insight/copilot/CopilotContextBar.vue'), 'utf8')
    expect(code).toMatch(/__field::before\s*{[^}]*width: 1px;[^}]*height: 12px;[^}]*background: var\(--color-border\)/)
    expect(code).toMatch(/__summary\s*{[^}]*flex-wrap: wrap/)
    expect(code).not.toMatch(/__created\s*{[^}]*display: none/)
  })
})
