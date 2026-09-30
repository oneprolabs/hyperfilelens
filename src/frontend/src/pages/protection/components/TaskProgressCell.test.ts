// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { ElProgress } from 'element-plus'
import { describe, expect, it } from 'vitest'

import TaskProgressCell from './TaskProgressCell.vue'
import { enProtectionPages } from '../../../locales/enProtectionPages'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      protection: {
        taskProgress: {
          ...enProtectionPages.taskProgress,
          bytesTransferred: '{size} transferred',
          bytesProcessed: 'Processed: {size}',
          bytesCapacity: '{done} / {total}',
          bytesProcessedCapacity: 'Processed: {done} / {total}',
          bytesCapacityEst: 'Incremental transfer: {done} / est. {total}',
          bytesCapacityRef: 'Transferred: {done} / source data: {total}',
          restoreBytesCapacity: 'Data restored: {done} / {total}',
          restoreSpeed: 'Restore speed: {speed}',
          restoreEtaHoursMinutes: '{h}h {m}m remaining',
          restoreEtaMinutes: '{n} min remaining',
          etaMinutes: '{n} min left',
          hashSpeed: 'Scanning: {speed}',
          processingSpeed: 'Processing speed: {speed}',
          uploadSpeed: 'Upload: {speed}',
          transfer: {
            hashedOnly: 'Backing up',
            uploadedAndHashed: 'Backing up',
          },
          restore: {
            running: 'Restoring',
            transferring: 'Restoring · {done}/{total} items restored',
          },
          stopping: {
            backup: 'Stopping backup…',
            restore: 'Stopping restore…',
          },
        },
      },
    },
  },
})

function mountCell(
  overrides: Record<string, unknown> = {},
  stopping = false,
) {
  return mount(TaskProgressCell, {
    props: {
      stopping,
      transferProgress: {
        phase: 'transferring',
        label_key: 'protection.taskProgress.transfer.hashedOnly',
        label_args: { hashed: 947 },
        progress_schema_version: 2,
        processed_bytes: 900_000_000,
        bytes_done: 900_000_000,
        bytes_total: 322_000_000_000,
        bytes_total_known: true,
        bytes_total_reference: true,
        processing_speed_bps: 19_293_000,
        upload_speed_bps: 5_740_000,
        eta_seconds: 900,
        step3_display_percent: 0.28,
        ...overrides,
      },
    },
    global: {
      plugins: [i18n],
      components: { ElProgress },
    },
  })
}

describe('TaskProgressCell', () => {
  it.each(['preparingLogic', 'preparing', 'dispatching', 'estimating'])(
    'hides backup graphics and residual metrics without bytes during %s', (stage) => {
      const wrapper = mountCell({
        phase: 'preparing',
        label_key: `protection.taskProgress.backup.${stage}`,
        show_metrics: true,
        bytes_done: 0,
        processed_bytes: 0,
        step3_display_percent: 0,
      })
      expect(wrapper.findComponent(ElProgress).exists()).toBe(false)
      expect(wrapper.find('.task-progress-cell__percent').exists()).toBe(false)
      expect(wrapper.find('.task-progress-cell__metric-line').exists()).toBe(false)
      expect(wrapper.find('.task-progress-cell__spinner').exists()).toBe(true)
    },
  )

  it('switches cleanly from preparation to backup and finalizing', async () => {
    const wrapper = mountCell()
    const active = { ...wrapper.props('transferProgress')! }
    await wrapper.setProps({ transferProgress: {
      ...active, label_key: 'protection.taskProgress.backup.preparing', show_metrics: true,
    } })
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Preparing backup…')
    expect(wrapper.findComponent(ElProgress).exists()).toBe(true)
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toContain('Backup progress:')
    await wrapper.setProps({ transferProgress: active })
    expect(wrapper.findComponent(ElProgress).exists()).toBe(true)
    expect(wrapper.text()).toContain('Backup progress:')
    expect(wrapper.text()).toContain('About 15 min remaining')
    await wrapper.setProps({ transferProgress: {
      ...active, phase: 'finalizing', label_key: 'protection.taskProgress.backup.finalizing',
      show_metrics: false, bytes_total_reference: false,
    } })
    expect(wrapper.findComponent(ElProgress).exists()).toBe(true)
    expect(wrapper.text()).toContain('Backup progress:')
    expect(wrapper.text()).not.toContain('remaining')
    wrapper.unmount()
  })

  it('retains known backup progress when the same task switches back to preparing', async () => {
    const wrapper = mountCell()
    await wrapper.setProps({ transferProgress: {
      ...wrapper.props('transferProgress')!,
      phase: 'preparing',
      label_key: 'protection.taskProgress.backup.preparing',
      show_metrics: false,
      eta_seconds: null,
    } })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Preparing backup…')
    expect(wrapper.get('.task-progress-cell__bar-row .task-progress-cell__percent').text()).toBe('0.28%')
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toBe('Backup progress: 858 MB / 300 GB')
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe([
      'Preparing backup…',
      '0.28%',
      'Backup progress: 858 MB / 300 GB',
    ].join('\n'))

    await wrapper.setProps({ transferProgress: {
      ...wrapper.props('transferProgress')!,
      phase: 'transferring',
      label_key: 'protection.taskProgress.transfer.hashedOnly',
    } })
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Backing up')
    expect(wrapper.get('.task-progress-cell__bar-row .task-progress-cell__percent').text()).toBe('0.28%')
  })

  it('keeps backup bytes but no bar when preparing with an unknown total', () => {
    const wrapper = mountCell({
      phase: 'preparing',
      label_key: 'protection.taskProgress.backup.preparing',
      bytes_total: null,
      bytes_total_known: false,
      step3_display_percent: null,
      show_metrics: false,
      eta_seconds: null,
    })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Preparing backup…')
    expect(wrapper.findComponent(ElProgress).exists()).toBe(false)
    expect(wrapper.find('.task-progress-cell__percent').exists()).toBe(false)
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toBe('Backup progress: 858 MB')
  })

  it('does not render a misleading bar for the initial preparing state with only a reference total', () => {
    const wrapper = mountCell({
      phase: 'preparing',
      label_key: 'protection.taskProgress.backup.preparing',
      bytes_done: 0,
      processed_bytes: 0,
      step3_display_percent: 0,
      show_metrics: true,
    })
    expect(wrapper.findComponent(ElProgress).exists()).toBe(false)
    expect(wrapper.find('.task-progress-cell__metrics').exists()).toBe(false)
  })

  it('restores the backup progress bar and percentage while preserving orchestration', () => {
    const wrapper = mountCell()

    expect(wrapper.findComponent(ElProgress).props('percentage')).toBe(0.28)
    expect(wrapper.find('.task-progress-cell__percent').exists()).toBe(true)
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Backing up')
    expect(wrapper.get('.task-progress-cell__label').attributes('title')).toBeUndefined()
    expect(wrapper.text()).toContain('%')
    expect(wrapper.text()).not.toContain('947')
  })

  it('does not add a percentage when the byte total is unknown', () => {
    const wrapper = mountCell({
      bytes_total: null,
      bytes_total_known: false,
    })

    expect(wrapper.find('.task-progress-cell__percent').exists()).toBe(false)
    expect(wrapper.findComponent(ElProgress).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('%')
  })

  it('keeps comparison and backup progress in separate lines without an empty bar', () => {
    const wrapper = mountCell({
      phase: 'transferring',
      bytes_total: null,
      bytes_total_known: false,
      step3_display_percent: null,
      comparison: {
        label_key: 'protection.taskProgress.backup.comparing',
        label_args: { done: 0, total: 1 },
        phase_elapsed_seconds: 10,
      },
    })
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe(
      'Comparing files · 0/1 directories completed · Active for 10s',
    )
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toBe('Backup progress: 858 MB')
    expect(wrapper.find('.task-progress-cell__bar-row').exists()).toBe(false)
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe([
      'Comparing files · 0/1 directories completed · Active for 10s',
      'Backup progress: 858 MB',
    ].join('\n'))
  })

  it('does not reserve a metric line when comparison has no byte amount', () => {
    const wrapper = mountCell({
      phase: 'estimating',
      label_key: 'protection.taskProgress.backup.comparing',
      label_args: { done: 0, total: 1 },
      bytes_done: 0,
      processed_bytes: 0,
      bytes_total: null,
      bytes_total_known: false,
      step3_display_percent: null,
      comparison: {
        label_key: 'protection.taskProgress.backup.comparing',
        label_args: { done: 0, total: 1 },
        phase_elapsed_seconds: 10,
      },
    })
    expect(wrapper.find('.task-progress-cell__metrics').exists()).toBe(false)
    expect(wrapper.find('.task-progress-cell__bar-row').exists()).toBe(false)
    expect(wrapper.get('.task-progress-cell__row1').classes()).toContain('is-last')
    const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskProgressCell.vue'), 'utf8')
    expect(source).not.toMatch(/\.task-progress-cell\s*{[^}]*min-height:\s*76px/s)
  })

  it('shows a valid bar during comparison even before the aggregate transfer phase', () => {
    const wrapper = mountCell({
      phase: 'estimating',
      label_key: 'protection.taskProgress.backup.comparing',
      label_args: { done: 0, total: 1 },
      comparison: {
        label_key: 'protection.taskProgress.backup.comparing',
        label_args: { done: 0, total: 1 },
        phase_elapsed_seconds: 10,
      },
    })
    expect(wrapper.get('.task-progress-cell__label-text').text()).toContain('Comparing files')
    expect(wrapper.get('.task-progress-cell__bar-row .task-progress-cell__percent').text()).toBe('0.28%')
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toContain('Backup progress:')
  })

  it('shows the percentage beside the bar with comparison and removes stale comparison', async () => {
    const wrapper = mountCell({
      comparison: {
        label_key: 'protection.taskProgress.backup.comparing',
        label_args: { done: 0, total: 1 },
        phase_elapsed_seconds: 10,
      },
    })
    expect(wrapper.get('.task-progress-cell__bar-row .task-progress-cell__percent').text()).toBe('0.28%')
    expect(wrapper.get('.task-progress-cell__label-text').text()).toContain('Comparing files')
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toContain('Backup progress:')
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toContain('Comparing files')

    await wrapper.setProps({ transferProgress: {
      ...wrapper.props('transferProgress')!,
      comparison: null,
    } })
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Backing up')
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).not.toContain('Comparing files')

    await wrapper.setProps({ transferProgress: {
      phase: 'estimating',
      label_key: 'protection.taskProgress.backup.estimating',
      bytes_done: 0,
      bytes_total_known: false,
      step3_display_percent: null,
    } })
    expect(wrapper.find('.task-progress-cell__metric-line').exists()).toBe(false)
    expect(wrapper.findComponent(ElProgress).exists()).toBe(false)
  })

  it('does not show stale comparison or progress while the agent reconnects', () => {
    const wrapper = mountCell({
      execution_state: 'reconnecting',
      comparison: {
        label_key: 'protection.taskProgress.backup.comparing',
        label_args: { done: 0, total: 1 },
        phase_elapsed_seconds: 10,
      },
    })
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Reconnecting to agent…')
    expect(wrapper.find('.task-progress-cell__metric-line').exists()).toBe(false)
    expect(wrapper.findComponent(ElProgress).exists()).toBe(false)
  })

  it('keeps the stopping state and warning progress bar', () => {
    const wrapper = mountCell({}, true)

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Stopping backup…')
    expect(wrapper.find('.task-progress-cell__spinner').exists()).toBe(false)
    expect(wrapper.findComponent(ElProgress).props('status')).toBe('warning')
  })

  it('hides Kopia hashed and uploaded counters from user-facing labels', () => {
    const wrapper = mountCell({
      label_key: 'protection.taskProgress.transfer.uploadedAndHashed',
      label_args: { uploaded: 123, hashed: 947 },
    })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Backing up')
    expect(wrapper.text()).not.toContain('123')
    expect(wrapper.text()).not.toContain('947')
  })

  it('shows the backup phase, directory counts, and elapsed time', () => {
    const wrapper = mountCell({
      phase: 'estimating',
      label_key: 'protection.taskProgress.backup.comparing',
      label_args: { done: 2, total: 5 },
      phase_elapsed_seconds: 125,
    })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe(
      'Comparing files · 2/5 directories completed · Active for 2 min',
    )
  })

  it('preserves restore orchestration and transfer metrics without a percentage', () => {
    const wrapper = mountCell({
      label_key: 'protection.taskProgress.restore.transferring',
      label_args: { done: 72_592, total: 333_000 },
      step3_display_percent: 10.2,
    })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Restoring')
    expect(wrapper.find('.task-progress-cell__percent').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('%')
    expect(wrapper.get('.task-progress-cell__metric-line').text()).toBe(
      'Data restored: 858 MB / 300 GB · 5.47 MB/s',
    )
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe([
      'Restoring · 72592/333000 items restored',
      'Data restored: 858 MB / 300 GB',
      'Restore speed: 5.47 MB/s',
      '15 min remaining',
    ].join('\n'))
  })

  it('labels hash throughput and exposes only the metric tooltip', () => {
    const wrapper = mountCell({
      progress_schema_version: 1,
      bytes_done: 0,
      bytes_total: null,
      bytes_total_known: false,
      upload_speed_bps: null,
      processing_speed_bps: null,
      speed_bps: 393_000_000,
      hash_speed_bps: 393_000_000,
      eta_seconds: null,
    })
    expect(wrapper.find('.task-progress-cell__metric-line').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('MB/s')

  })

  it('keeps backup metrics in the original single-line layout', () => {
    const wrapper = mountCell()
    const line = wrapper.get('.task-progress-cell__metric-line')

    expect(line.text()).toBe('Backup progress: 858 MB / 300 GB · About 15 min remaining')
    expect(line.element.children).toHaveLength(0)

    const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskProgressCell.vue'), 'utf8')
    expect(source).toMatch(/\.task-progress-cell__metric-line\s*{[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s)
  })

  it('provides structured overflow tooltip text without standalone separators', () => {
    const wrapper = mountCell()

    expect(wrapper.get('.task-progress-cell').attributes()).toHaveProperty('data-table-overflow-explicit-only')
    expect(wrapper.get('.task-progress-cell').attributes()).toHaveProperty('data-table-overflow-title-always')
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe([
      'Backing up',
      '0.28%',
      'Backup progress: 858 MB / 300 GB',
      'About 15 min remaining',
    ].join('\n'))
  })

  it('keeps backup progress without speed in the hover text when the total is unknown', () => {
    const wrapper = mountCell({
      bytes_total: null,
      bytes_total_known: false,
      processing_speed_bps: 8.74 * 1024 * 1024,
      upload_speed_bps: null,
      eta_seconds: null,
      step3_display_percent: null,
    })

    const title = [
      'Backing up',
      'Backup progress: 858 MB',
    ].join('\n')
    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe(title)
  })

  it('keeps the full phase and queue state available when the label is truncated', () => {
    const wrapper = mountCell({
      phase: 'queued',
      label_key: 'protection.taskProgress.backup.queued',
      label_args: { queued: 3, done: 0, total: 3 },
      bytes_total: null,
      bytes_total_known: false,
      step3_display_percent: null,
      processing_speed_bps: null,
      upload_speed_bps: null,
      eta_seconds: null,
    })

    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe(
      'Waiting for backup capacity · 3 queued · 0/3 directories completed',
    )
    expect(wrapper.find('.task-progress-cell__metric-line').exists()).toBe(false)
  })

  it('combines orchestration and backup progress in one hover title', () => {
    const wrapper = mountCell({
      phase: 'transferring',
      label_key: 'protection.taskProgress.backup.uploadingQueued',
      label_args: { running: 2, queued: 3, done: 1, total: 6 },
    })

    const title = [
      'Uploading changed data · 2 running · 3 queued · 1/6 directories completed',
      '0.28%',
      'Backup progress: 858 MB / 300 GB',
      'About 15 min remaining',
    ].join('\n')

    expect(wrapper.get('.task-progress-cell').attributes('data-table-overflow-title')).toBe(title)
  })
})
