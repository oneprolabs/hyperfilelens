import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { computed } from 'vue'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import { operationalEventDisplay } from './operationalEventDisplay'

const zh = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'), 'utf8',
))
const event = {
  event_type: 'task.failed',
  title: 'Repository operation failed: Orphan snapshot reconciliation · huawei-obs-backup-repo',
  details: 'Snapshot reconciliation failed; no unconfirmed deletion will be retried.',
}

function translator(locale = 'zh-hans') {
  return createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: { en, 'zh-hans': zh } }).global
}

describe('operational event localization', () => {
  it('translates the reported historical event without mutating it', () => {
    const original = { ...event }
    const { t } = translator()
    expect(operationalEventDisplay(event, t)).toEqual({
      title: '存储仓库操作失败：孤立快照对账 · huawei-obs-backup-repo',
      details: '快照对账失败；不会重试任何未经确认的删除操作。',
    })
    expect(event).toEqual(original)
  })

  it('preserves the English text and reacts to locale switching without refetching', () => {
    const i18n = translator('en')
    const display = computed(() => operationalEventDisplay(event, i18n.t))
    expect(display.value).toEqual({ title: event.title, details: event.details })
    i18n.locale.value = 'zh-hans'
    expect(display.value.title).toContain('存储仓库操作失败')
  })

  it.each([
    'Quick maintenance', 'Full maintenance', 'Orphan snapshot reconciliation',
    'Delete subrepository', 'Delete repository', 'Check', 'Update Credentials',
    'Create repository', 'Bind proxy', 'Remount repository',
  ])('translates %s while preserving the complete resource name', (action) => {
    const { t } = translator()
    const display = operationalEventDisplay({
      event_type: 'task.timeout',
      title: `Repository operation timeout: ${action} · repo · Check: 中文`,
      details: 'Unknown engine diagnostic: failed with exit code 7',
    }, t)
    expect(display.title).toMatch(/^存储仓库操作超时：.+ · repo · Check: 中文$/)
    expect(display.title).not.toContain(`${action} · repo`)
    expect(display.details).toBe('Unknown engine diagnostic: failed with exit code 7')
  })

  it('retains unknown actions and does not translate unrelated event evidence', () => {
    const { t } = translator()
    expect(operationalEventDisplay({
      ...event, title: 'Repository operation failed: Future operation · repo',
    }, t).title).toBe('存储仓库操作失败：Future operation · repo')
    for (const event_type of ['task.success', 'repository.offline', undefined]) {
      expect(operationalEventDisplay({ ...event, event_type }, t))
        .toEqual({ title: event.title, details: event.details })
    }
  })

  it('uses the shared formatter in both event surfaces and the details drawer', () => {
    const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')
    const events = readFileSync(resolve(process.cwd(), 'src/pages/ops/Events.vue'), 'utf8')
    expect(dashboard).toContain('v-for="item in displayEvents"')
    expect(dashboard).toContain('operationalEventDisplay({ ...event, details: event.detail }, t)')
    expect(events).toContain(':data="displayEvents"')
    expect(events).toContain('operationalEventDisplay(rawSelectedEvent.value, t)')
  })
})
