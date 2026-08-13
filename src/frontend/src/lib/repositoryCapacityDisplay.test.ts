import { describe, expect, it } from 'vitest'

import { remainingLimitExceedsAvailableStorage } from './repositoryCapacityDisplay'

describe('remainingLimitExceedsAvailableStorage', () => {
  it('warns only when remaining limit exceeds successfully collected available storage', () => {
    expect(remainingLimitExceedsAvailableStorage({
      configuredLimitBytes: 500,
      estimatedUsageBytes: 100,
      storageAvailableBytes: 200,
      usageProbeStatus: 'success',
      capacityProbeStatus: 'success',
    })).toBe(true)
    expect(remainingLimitExceedsAvailableStorage({
      configuredLimitBytes: 300,
      estimatedUsageBytes: 100,
      storageAvailableBytes: 200,
      usageProbeStatus: 'success',
      capacityProbeStatus: 'success',
    })).toBe(false)
    expect(remainingLimitExceedsAvailableStorage({
      configuredLimitBytes: 250,
      estimatedUsageBytes: 100,
      storageAvailableBytes: 200,
      usageProbeStatus: 'success',
      capacityProbeStatus: 'success',
    })).toBe(false)
  })

  it('treats a successful zero available value as valid', () => {
    expect(remainingLimitExceedsAvailableStorage({
      configuredLimitBytes: 2,
      estimatedUsageBytes: 1,
      storageAvailableBytes: 0,
      usageProbeStatus: 'success',
      capacityProbeStatus: 'success',
    })).toBe(true)
  })

  it('does not warn for unavailable usage, unavailable capacity, or unsupported backing storage', () => {
    expect(remainingLimitExceedsAvailableStorage({
      configuredLimitBytes: 500,
      estimatedUsageBytes: 0,
      storageAvailableBytes: 0,
      usageProbeStatus: 'pending',
      capacityProbeStatus: 'success',
    })).toBe(false)
    for (const capacityProbeStatus of ['pending', 'failed']) {
      expect(remainingLimitExceedsAvailableStorage({
        configuredLimitBytes: 500,
        estimatedUsageBytes: 100,
        storageAvailableBytes: 0,
        usageProbeStatus: 'success',
        capacityProbeStatus,
      })).toBe(false)
    }
    expect(remainingLimitExceedsAvailableStorage({
      configuredLimitBytes: 500,
      estimatedUsageBytes: 100,
      storageAvailableBytes: 0,
      usageProbeStatus: 'success',
      capacityProbeStatus: 'success',
      supportsBackingStorage: false,
    })).toBe(false)
  })
})
