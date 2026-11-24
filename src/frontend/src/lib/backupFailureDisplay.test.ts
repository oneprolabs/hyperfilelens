import { describe, expect, it } from 'vitest'
import { backupFailureCategory, backupFailureMetadata, backupFailurePresentation } from './backupFailureDisplay'

describe('backup failure compatibility', () => {
  it('shows upgrade guidance instead of treating an old Agent as a connection failure', () => {
    const input = { error_code: 'AGENT_UPGRADE_REQUIRED', error_message: 'Internal diagnostic' }
    expect(backupFailureCategory(input)).toBe('agent_upgrade_required')
    expect(backupFailurePresentation(input)?.reason).toContain('must be upgraded')
    expect(backupFailurePresentation(input)?.resolutions[0]).toContain('Source Hosts')
    expect(backupFailureMetadata(input).error_message).toBe(input.error_message)
  })
  it('recognizes legacy dictionaries without metadata and preserves the diagnostic', () => {
    const input = { error_message: "{'source_ref_id': ['Agent source is offline.']}" }
    expect(backupFailurePresentation(input)?.reason).toContain('was offline')
    expect(backupFailureMetadata(input).error_message).toBe(input.error_message)
  })
  it('reads task result payloads when recent events are absent', () => {
    expect(backupFailurePresentation({ result_payload: { failure_details: { category: 'backup_source_busy' } } })?.reason).toContain('was busy')
  })
  it('does not classify unrelated prechecks as offline', () => {
    expect(backupFailurePresentation({ error_code: 'BACKUP_PRECHECK_FAILED', error_message: 'Missing directory' })?.reason).toContain('prerequisite')
  })
  it('preserves existing structured errors ahead of legacy text', () => {
    expect(backupFailureMetadata({ failure_details: { category: 'permission_denied' }, error_message: 'Agent source is offline.' }).failure_details).toEqual({ category: 'permission_denied' })
  })
  it('classifies acknowledgement timeouts without changing the stored error', () => {
    const input = { error_code: 'AGENT_ACK_TIMEOUT', error_message: 'The Agent did not acknowledge the backup command.' }
    expect(backupFailureCategory(input)).toBe('backup_communication_timeout')
    expect(backupFailurePresentation(input)?.reason).toContain('could not be confirmed')
    expect(backupFailureMetadata(input).error_code).toBe(input.error_code)
  })
})
