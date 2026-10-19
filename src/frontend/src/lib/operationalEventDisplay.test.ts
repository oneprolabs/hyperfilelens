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
const repositoryTask = zh.ops.events.repositoryTask
const repositoryOperations = zh.ops.events.repositoryOperations
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
      title: repositoryTask.failed.replace('{name}', `${repositoryOperations.reconcile} · huawei-obs-backup-repo`),
      details: repositoryTask.reconciliationFailed,
    })
    expect(event).toEqual(original)
  })

  it('preserves the English text and reacts to locale switching without refetching', () => {
    const i18n = translator('en')
    const display = computed(() => operationalEventDisplay(event, i18n.t))
    expect(display.value).toEqual({ title: event.title, details: event.details })
    i18n.locale.value = 'zh-hans'
    expect(display.value.title).toBe(
      repositoryTask.failed.replace('{name}', `${repositoryOperations.reconcile} · huawei-obs-backup-repo`),
    )
  })

  it.each([
    'Quick maintenance', 'Full maintenance', 'Orphan snapshot reconciliation',
    'Delete subrepository', 'Delete repository', 'Check', 'Update Credentials',
    'Create repository', 'Bind proxy', 'Remount repository',
  ])('translates %s while preserving the complete resource name', (action) => {
    const { t } = translator()
    const resourceName = `repo · Check: ${repositoryOperations.check}`
    const display = operationalEventDisplay({
      event_type: 'task.timeout',
      title: `Repository operation timeout: ${action} · ${resourceName}`,
      details: 'Unknown engine diagnostic: failed with exit code 7',
    }, t)
    expect(display.title).toMatch(
      new RegExp(`^${repositoryTask.timeout.replace('{name}', '.+')} · ${resourceName}$`),
    )
    expect(display.title).not.toContain(`${action} · repo`)
    expect(display.details).toBe('Unknown engine diagnostic: failed with exit code 7')
  })

  it('retains unknown actions and does not translate unrelated event evidence', () => {
    const { t } = translator()
    expect(operationalEventDisplay({
      ...event, title: 'Repository operation failed: Future operation · repo',
    }, t).title).toBe(repositoryTask.failed.replace('{name}', 'Future operation · repo'))
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
