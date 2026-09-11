import { describe, expect, it } from 'vitest'
import { backupFailureMetadata, backupFailurePresentation } from './backupFailureDisplay'

describe('backup failure compatibility', () => {
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
})
