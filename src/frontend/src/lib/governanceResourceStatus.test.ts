import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import {
  governanceResourceStatus,
  governanceResourceStatusDetail,
  governanceResourceStatusLabel,
  governanceResourceStatusTagAttrs,
  type ResourceStatus,
} from './governanceResourceStatus'

const translate = (key: string) => key.split('.').reduce(
  (value, segment) => (value as Record<string, unknown>)[segment],
  en as unknown,
) as string

describe('governance resource status', () => {
  const maps: Record<string, Record<string, ResourceStatus>> = {
    node: {
      active: 'active', upgrading: 'updating', restarting: 'updating',
      verifying: 'verifying', verification_pending: 'verifying',
      removing: 'removing', cleaning_up: 'removing', failed: 'failed',
      upgrade_failed: 'failed', deregistration_failed: 'failed',
    },
    repository: {
      created: 'active', creating: 'creating', removing: 'removing',
      create_failed: 'failed', remove_failed: 'failed',
    },
    source_resource: {
      active: 'active', inactive: 'inactive', probing: 'verifying',
      removing: 'removing', error: 'failed', remove_failed: 'failed',
    },
    backup_config: {
      active: 'active', provisioning: 'creating', resetting: 'updating',
      provision_failed: 'failed', reset_failed: 'failed',
    },
  }
  for (const kind of ['restore_plan', 'backup_policy', 'file_filter_rule', 'alert_policy', 'notification_channel']) {
    maps[kind] = { active: 'active', inactive: 'inactive' }
  }
  const cases = Object.entries(maps).flatMap(([kind, states]) =>
    Object.entries(states).map(([raw, status]) => ({ kind, raw, status })))

  it.each(cases)('maps legacy $kind/$raw to $status', ({ kind, raw, status }) => {
    expect(governanceResourceStatus({ resource_type: kind, status: raw })).toBe(status)
    const detail = governanceResourceStatusDetail({ resource_type: kind, status: raw }, translate)
    expect(detail).not.toContain('settings.resourceStatusDetails.')
    expect(detail).not.toBeUndefined()
  })

  it.each([
    { resource_type: 'future', status: 'active' },
    { resource_type: 'node', status: 'online' },
    { resource_type: 'repository', status: 'active' },
    { resource_type: 'node', status: 'created' },
    { resource_type: 'node', status: 'toString' },
    { resource_type: 'constructor', status: 'active' },
    { resource_type: 'node', status: 'future_failed' },
    { resource_type: 'node', status: null },
    { resource_type: 'node', status: 'private-diagnostic-token' },
  ])('fails neutral for unsupported states: %j', (row) => {
    expect(governanceResourceStatus(row)).toBe('unknown')
    expect(governanceResourceStatusDetail(row, translate)).toBe('')
  })

  it('uses a valid server projection and does not guess when it is invalid', () => {
    expect(governanceResourceStatus({ resource_status: 'updating', status: 'upgrading' })).toBe('updating')
    expect(governanceResourceStatus({ resource_status: 'future', resource_type: 'node', status: 'active' })).toBe('unknown')
    expect(governanceResourceStatus({ resource_status: null, resource_type: 'node', status: 'active' })).toBe('unknown')
    expect(governanceResourceStatus({ resource_status: 'constructor' })).toBe('unknown')
  })

  it('retains specific known failure details without exposing arbitrary text', () => {
    expect(governanceResourceStatusDetail({ resource_type: 'node', status: 'upgrade_failed' }, translate)).toBe('Upgrade failed')
    expect(governanceResourceStatusDetail({ resource_type: 'repository', status: 'created' }, translate)).toBe('Created')
    expect(governanceResourceStatusDetail({ resource_type: 'alert_policy', status: 'active' }, translate)).toBe('Enabled')
    expect(governanceResourceStatusDetail({ resource_type: 'node', status: 'active' }, translate)).toBe('')
  })

  it.each([
    ['active', 'success'], ['inactive', undefined], ['creating', 'info'],
    ['updating', 'info'], ['verifying', 'info'], ['removing', 'warning'],
    ['failed', 'danger'], ['unknown', undefined],
  ] as const)('uses the agreed tag tone for %s', (status, tone) => {
    expect(governanceResourceStatusTagAttrs(status).type).toBe(tone)
    expect(governanceResourceStatusLabel(status, translate)).toBeDefined()
    if (tone === undefined) {
      expect(governanceResourceStatusTagAttrs(status).class).toBe('hfl-tag--neutral')
    }
  })
})
