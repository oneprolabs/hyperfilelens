// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import NodeUpgradeProgress from './NodeUpgradeProgress.vue'
import type { NodeLifecycleInfo } from '../../types/nodeLifecycle'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      nodeUpgradeProgress: {
        title: 'Agent Upgrade',
        completed: 'Completed',
        inProgress: 'In Progress',
        pending: 'Pending',
        failed: 'Failed',
        noActiveUpgrade: 'No active upgrade',
        preparingDownload: 'Preparing Agent package download',
        downloadingPackage: 'Downloading Agent package',
        waitingForData: 'Waiting for download data',
        retryingDownload: 'Download interrupted. Retrying…',
        downloadCompleted: 'Agent package downloaded',
        downloadedAmount: '{amount} downloaded',
        elapsed: 'Elapsed {duration}',
        attempt: 'Attempt {attempt} of {max}',
        secondsShort: '{n}s',
        minutesShort: '{n}m',
        hoursMinutesShort: '{h}h {m}m',
      },
    },
  },
})

function lifecycle(download: NodeLifecycleInfo['download']): NodeLifecycleInfo {
  return {
    kind: 'upgrade',
    state: 'upgrading',
    current_version: '0.2.11',
    target_version: '0.2.12',
    timeline: [
      {
        phase: 'upgrading',
        label: 'Upgrading',
        at: null,
        status: 'active',
      },
    ],
    download,
  }
}

function mountProgress(download: NodeLifecycleInfo['download']) {
  return mount(NodeUpgradeProgress, {
    props: { lifecycle: lifecycle(download) },
    global: { plugins: [i18n] },
  })
}

describe('NodeUpgradeProgress download details', () => {
  it('shows compact transfer metrics in the existing upgrade step', () => {
    const wrapper = mountProgress({
      state: 'downloading',
      downloaded_bytes: 8 * 1024 * 1024,
      total_bytes: 21.6 * 1024 * 1024,
      bytes_per_second: 5 * 1024 * 1024,
      elapsed_seconds: 11,
      attempt: 1,
      max_attempts: 3,
    })

    expect(wrapper.text()).toContain('Downloading Agent package')
    expect(wrapper.text()).toContain('8.00 MB / 21.6 MB')
    expect(wrapper.text()).toContain('5.00 MB/s')
    expect(wrapper.text()).toContain('Elapsed 11s')
    expect(wrapper.text()).toContain('Attempt 1 of 3')
  })

  it('shows the upcoming attempt while waiting to retry', () => {
    const wrapper = mountProgress({
      state: 'retry_wait',
      attempt: 1,
      next_attempt: 2,
      max_attempts: 3,
    })

    expect(wrapper.text()).toContain('Download interrupted. Retrying…')
    expect(wrapper.text()).toContain('Attempt 2 of 3')
  })
})
