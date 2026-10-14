import { describe, expect, it } from 'vitest'
import { governanceResourceTypeLabel } from './governanceResourceTypes'
import { en } from '../locales/en'

describe('governance resource type labels', () => {
  const types = [
    'Source Host', 'Source NAS', 'Proxy Host', 'Object Storage', 'Target NAS',
    'Local Disk', 'Private Data Gateway', 'Backup Configuration', 'Restore Plan',
    'Backup Policy', 'File Filter', 'Alert Rule', 'Notification Channel',
  ]

  it.each(types)('uses a singular English label for %s', (type) => {
    const translate = (key: string) => {
      const leaf = key.split('.').at(-1) as keyof typeof en.settings.resourceTypes
      return en.settings.resourceTypes[leaf]
    }
    expect(governanceResourceTypeLabel(type, translate)).toBe(type)
  })

  it('keeps unknown labels readable instead of showing a missing translation key', () => {
    expect(governanceResourceTypeLabel('Future Type', () => 'missing')).toBe('Future Type')
  })
})
