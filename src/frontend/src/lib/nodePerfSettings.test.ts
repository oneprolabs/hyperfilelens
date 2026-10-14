import { describe, expect, it } from 'vitest'

import {
  buildDefaultNodePerfSettings,
  mergeNodeMetadataWithPerfSettings,
  readNodePerfSettings,
} from './nodePerfSettings'

describe('node repository cache settings', () => {
  it('reads legacy console parser spelling without changing stored data', () => {
    const node = { metadata: { perf_settings: {
      cpu_cores: 3, mem_cap_mb: 2050, kopia_cache_mb: 0,
      log_retention: 14, log_space_mb: 256,
    } } }
    const settings = readNodePerfSettings(node, buildDefaultNodePerfSettings(8, 16384))
    expect(settings).toMatchObject({ cpuCores: 3, memCapMb: 2050, kopiaCacheMb: 0, logRetention: 14, logSpaceMb: 256 })
    expect(node.metadata.perf_settings).not.toHaveProperty('cpuCores')
  })
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
