import { describe, expect, it } from 'vitest'
import { cacheFailureDetails } from './cacheFailureDisplay'

const message = 'CACHE_SPACE_INSUFFICIENT: cache "/Volumes/cache" has 48494751744 available bytes; backup requires 53687091200 bytes (warning 64424509440); free space, expand the volume or safely relocate cache, then run a new backup'

describe('cacheFailureDetails', () => {
  it('reads existing Agent diagnostics without changing saved errors', () => {
    expect(cacheFailureDetails({ error_message: `[CACHE_SPACE_INSUFFICIENT] ${message}` })).toMatchObject({
      code: 'CACHE_SPACE_INSUFFICIENT', path: '/Volumes/cache', available: 48494751744,
      blocking: 53687091200, warning: 64424509440,
    })
  })

  it('prefers structured facts over legacy values and does not confuse the source with the cache path', () => {
    expect(cacheFailureDetails({ result_payload: { terminal_failure: {
      error_code: 'CACHE_SPACE_INSUFFICIENT', message,
      path: '/backup/source', cache_root: '/actual/cache',
      cache_space_check: { available_bytes: 0, policy: { blocking_bytes: 20, warning_bytes: 30 } },
    } } })).toMatchObject({ path: '/actual/cache', available: 0, blocking: 20, warning: 30 })
  })

  it('keeps failed queries unknown instead of reporting zero space', () => {
    expect(cacheFailureDetails({ error_code: 'CACHE_SPACE_CHECK_FAILED', cache_space_check: { available_bytes: 0 } })?.available).toBeUndefined()
  })

  it('handles missing and invalid measurements without invented capacities', () => {
    expect(cacheFailureDetails({ error_code: 'CACHE_SPACE_INSUFFICIENT', cache_space_check: {
      available_bytes: null, policy: { blocking_bytes: -1, warning_bytes: '60' },
    } })).toMatchObject({ available: undefined, blocking: undefined, warning: undefined })
  })

  it('supports projected errors and recent event metadata', () => {
    expect(cacheFailureDetails({ error_details: { error_code: 'CACHE_SPACE_CHECK_FAILED' } })?.code).toBe('CACHE_SPACE_CHECK_FAILED')
    expect(cacheFailureDetails({ recent_events: [{ metadata: { error_message: message } }] })?.path).toBe('/Volumes/cache')
  })

  it.each([undefined, null, {}, { error_code: 'BACKUP_TARGET_STORAGE_FULL', error_message: 'Disk full' },
    { error_message: 'A file named CACHE_SPACE_INSUFFICIENT could not be read' }])('leaves unrelated errors unchanged: %j', (value) => {
    expect(cacheFailureDetails(value)).toBeNull()
  })
})
