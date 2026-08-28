// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import TaskProgressCell from './TaskProgressCell.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      protection: {
        taskProgress: {
          bytesTransferred: '{size} transferred',
          bytesProcessed: 'Processed: {size}',
          bytesCapacity: '{done} / {total}',
          bytesProcessedCapacity: 'Processed: {done} / {total}',
          bytesCapacityEst: 'Incremental transfer: {done} / est. {total}',
          bytesCapacityRef: 'Transferred: {done} / source data: {total}',
          etaMinutes: '{n} min left',
          hashSpeed: 'Scanning: {speed}',
          processingSpeed: 'Processing speed: {speed}',
          uploadSpeed: 'Upload: {speed}',
          transfer: {
            hashedOnly: 'Backing up',
            uploadedAndHashed: 'Backing up',
          },
          restore: {
            transferring: 'Restoring',
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

const ElProgressStub = {
  props: ['percentage'],
  template: '<div class="el-progress-stub" :data-percentage="percentage" />',
}

function mountCell(
  progress: number | undefined,
  overrides: Record<string, unknown> = {},
  stopping = false,
) {
  return mount(TaskProgressCell, {
    props: {
      progress,
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
      stubs: { ElProgress: ElProgressStub },
    },
  })
}

describe('TaskProgressCell', () => {
  it('uses the logical snapshot percent while transferring', () => {
    const wrapper = mountCell(59.1)

    expect(wrapper.get('.task-progress-cell__percent').text()).toBe('0.28%')
    expect(wrapper.get('.el-progress-stub').attributes('data-percentage')).toBe('0.28')
    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Backing up')
    expect(wrapper.text()).not.toContain('947')
  })

  it('falls back to the byte-based display percent when task progress is unavailable', () => {
    const wrapper = mountCell(undefined)

    expect(wrapper.get('.task-progress-cell__percent').text()).toBe('0.28%')
    expect(wrapper.get('.el-progress-stub').attributes('data-percentage')).toBe('0.28')
  })

  it('does not show a numeric snapshot percent when the byte total is unknown', () => {
    const wrapper = mountCell(59.1, {
      bytes_total: null,
      bytes_total_known: false,
    })

    expect(wrapper.find('.task-progress-cell__percent').exists()).toBe(false)
    expect(wrapper.get('.el-progress-stub').attributes('data-percentage')).toBe('0')
  })

  it('keeps task progress stable while the task is stopping', () => {
    const wrapper = mountCell(59.1, {}, true)

    expect(wrapper.get('.task-progress-cell__percent').text()).toBe('59.10%')
    expect(wrapper.get('.el-progress-stub').attributes('data-percentage')).toBe('59.1')
  })

  it('hides Kopia hashed and uploaded counters from user-facing labels', () => {
    const wrapper = mountCell(59.1, {
      label_key: 'protection.taskProgress.transfer.uploadedAndHashed',
      label_args: { uploaded: 123, hashed: 947 },
    })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Backing up')
    expect(wrapper.text()).not.toContain('123')
    expect(wrapper.text()).not.toContain('947')
  })

  it('uses Step 3 progress for restore transfers too', () => {
    const wrapper = mountCell(65.4, {
      label_key: 'protection.taskProgress.restore.transferring',
      label_args: {},
      step3_display_percent: 10.2,
    })

    expect(wrapper.get('.task-progress-cell__label-text').text()).toBe('Restoring')
    expect(wrapper.get('.task-progress-cell__percent').text()).toBe('10.20%')
    expect(wrapper.get('.el-progress-stub').attributes('data-percentage')).toBe('10.2')
  })

  it('labels hash throughput and includes it in either overflow target tooltip', () => {
    const wrapper = mountCell(14.55, {
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
    const expectedTitle = 'Scanning: 375 MB/s'

    expect(wrapper.get('.task-progress-cell__metric-line').text()).toBe('Scanning: 375 MB/s')
    expect(wrapper.get('.task-progress-cell__label-text').attributes('data-table-overflow-title')).toBe(expectedTitle)
    expect(wrapper.get('.task-progress-cell__metric-line').attributes('data-table-overflow-title')).toBe(expectedTitle)
  })

  it('renders all metrics in one ellipsizing text node', () => {
    const wrapper = mountCell(59.1)
    const line = wrapper.get('.task-progress-cell__metric-line')

    expect(line.text()).toBe('Processed: 858 MB / 300 GB · 18.4 MB/s · 15 min left')
    expect(line.element.children).toHaveLength(0)

    const source = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskProgressCell.vue'), 'utf8')
    expect(source).toMatch(/\.task-progress-cell__metric-line\s*{[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s)
  })

  it('provides structured overflow tooltip text without standalone separators', () => {
    const wrapper = mountCell(59.1)

    expect(wrapper.get('.task-progress-cell__metric-line').attributes('data-table-overflow-title')).toBe([
      'Processed: 858 MB / 300 GB',
      'Processing speed: 18.4 MB/s',
      '15 min left',
    ].join('\n'))
  })
})
