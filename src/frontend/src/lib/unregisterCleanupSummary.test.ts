import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import { en } from '../locales/en'
import { summarizeUnregisterCleanup } from './unregisterCleanupSummary'

const t = createI18n({ legacy: false, locale: 'en', messages: { en } }).global.t
const generic = 'Remote uninstall was skipped because the Agent was offline.'

describe('unregister cleanup summary', () => {
  it('collapses multiple records of one offline uninstall before translating', () => {
    const result = summarizeUnregisterCleanup([
      { code: 'agent_offline', detail: generic, sourceId: 'agent:1' },
      { code: 'agent_offline', detail: 'Agent "Host A" is offline — uninstall was skipped.', sourceId: 'agent:1' },
      { detail: `${generic} Agent "Host A" is offline — uninstall was skipped.`, sourceName: 'Host A' },
    ], t)
    expect(result.reasons).toEqual([en.protection.backupsPage.unregisterOfflineCleanupCause])
    expect(result.failures).toEqual([])
    expect([...result.offlineTargets]).toEqual(['agent:1', 'Host A'])
  })

  it('preserves distinct affected objects and other cleanup operations', () => {
    const result = summarizeUnregisterCleanup([
      { detail: 'Agent "Host A" is offline — uninstall was skipped.', sourceId: 'agent:1' },
      { detail: 'Agent "Host B" is offline — uninstall was skipped.', sourceId: 'agent:2' },
      { code: 'repository_cleanup_failed', detail: 'Snapshot cleanup failed', sourceName: 'Repository C' },
    ], t)
    expect(result.offlineTargets.has('agent:1')).toBe(true)
    expect(result.offlineTargets.has('agent:2')).toBe(true)
    expect(result.offlineTargets.has('Repository C')).toBe(false)
    expect(result.reasons).toEqual([
      en.protection.backupsPage.unregisterOfflineCleanupCause,
      'Repository C: Snapshot cleanup failed',
    ])
    expect(result.failures).toEqual(['Snapshot cleanup failed'])
  })

  it('does not discard unknown evidence concatenated with known diagnostics', () => {
    const result = summarizeUnregisterCleanup([{ detail: `${generic} Custom cleanup problem: /test/path` }], t)
    expect(result.reasons).toContain('Custom cleanup problem: /test/path')
    expect(result.failures).toContain('Custom cleanup problem: /test/path')
    expect(result.offlineTargets.size).toBe(0)
  })

  it('deduplicates an unscoped aggregate when the same failure is source-scoped', () => {
    const result = summarizeUnregisterCleanup([
      { code: 'unmount_failed', detail: 'The NAS mount could not be removed.' },
      { code: 'unmount_failed', detail: 'The NAS mount could not be removed.', sourceName: 'NAS A' },
    ], t)
    expect(result.reasons).toEqual(['NAS A: The NAS mount could not be removed.'])
    expect(result.failures).toEqual(['The NAS mount could not be removed.'])
  })

  it('keeps identical failures for different sources', () => {
    const result = summarizeUnregisterCleanup([
      { code: 'unmount_failed', detail: 'The NAS mount could not be removed.', sourceName: 'NAS A' },
      { code: 'unmount_failed', detail: 'The NAS mount could not be removed.', sourceName: 'NAS B' },
    ], t)
    expect(result.reasons).toEqual([
      'NAS A: The NAS mount could not be removed.',
      'NAS B: The NAS mount could not be removed.',
    ])
  })

  it('does not merge different source IDs with identical names', () => {
    const result = summarizeUnregisterCleanup([
      { detail: 'Unmount failed', sourceName: 'NAS', sourceId: 'nas:1' },
      { detail: 'Unmount failed', sourceName: 'NAS', sourceId: 'nas:2' },
    ], t)
    expect(result.reasons).toEqual(['nas:1: Unmount failed', 'nas:2: Unmount failed'])
  })

  it('recognizes an unambiguous legacy numeric alias for the same source', () => {
    const result = summarizeUnregisterCleanup([
      { detail: 'Unmount failed', sourceName: 'NAS', sourceId: '32' },
      { detail: 'Unmount failed', sourceName: 'NAS', sourceId: 'nas:32' },
    ], t)
    expect(result.reasons).toEqual(['NAS: Unmount failed'])
  })
})
