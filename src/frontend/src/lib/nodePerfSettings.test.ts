import { describe, expect, it } from 'vitest'

import {
  buildDefaultNodePerfSettings,
  mergeNodeMetadataWithPerfSettings,
  readNodePerfSettings,
} from './nodePerfSettings'

describe('node repository cache settings', () => {
  it('defaults each source node repository cache to 1 GiB', () => {
    expect(buildDefaultNodePerfSettings(8, 16_384).kopiaCacheMb).toBe(1024)
  })

  it('preserves zero and rejects cache values outside the supported range', () => {
    const defaults = buildDefaultNodePerfSettings(8, 16_384)
    expect(
      readNodePerfSettings(
        { metadata: { perf_settings: { kopiaCacheMb: 0 } } },
        defaults,
      ).kopiaCacheMb,
    ).toBe(0)
    expect(
      readNodePerfSettings(
        { metadata: { perf_settings: { kopiaCacheMb: 65_537 } } },
        defaults,
      ).kopiaCacheMb,
    ).toBe(1024)
    expect(
      readNodePerfSettings(
        { metadata: { perf_settings: { kopiaCacheMb: null } } },
        defaults,
      ).kopiaCacheMb,
    ).toBe(1024)
    expect(
      readNodePerfSettings(
        { metadata: { perf_settings: { kopiaCacheMb: '2048' } } },
        defaults,
      ).kopiaCacheMb,
    ).toBe(1024)
  })

  it('does not persist repository cache settings for gateway nodes', () => {
    const settings = buildDefaultNodePerfSettings(8, 16_384)
    const metadata = mergeNodeMetadataWithPerfSettings(
      { role: 'gateway', metadata: {} },
      settings,
    )

    expect(metadata.perf_settings as Record<string, unknown>).not.toHaveProperty('kopiaCacheMb')
  })
})
