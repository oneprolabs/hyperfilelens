import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import { describe, expect, it } from 'vitest'
import type { ApiNode } from '../types/node'
import { blockingGiBToBytes, CACHE_CAPABILITY, cacheWarningBytes, GIB, bytesToGiBInput, nodeCacheCapability, nodeUsesDemandCache, localizeCacheError } from './nodeCacheApi'

describe('node cache management contract', () => {
  it('derives warning by adding ten GiB with exact byte parsing', () => {
    for (const value of [2, 10, 20, 21, 30, 50]) expect(cacheWarningBytes(blockingGiBToBytes(String(value))!)).toBe((value + 10) * GIB)
    expect(blockingGiBToBytes('0.000000001')).toBe(1)
    expect(blockingGiBToBytes('1.234567891')).toBe(1_325_607_179)
    for (const value of ['0', '-1', 'NaN', 'Infinity', '1e5', '9999999999999999999']) expect(blockingGiBToBytes(value)).toBeNull()
  })
  it('preserves every retained byte without a dirty or rounded threshold', () => {
    for (const bytes of [1, 10_000_000_000, 20_000_000_000, 1_234_567_891, Number.MAX_SAFE_INTEGER - 10 * GIB]) {
      expect(blockingGiBToBytes(bytesToGiBInput(bytes))).toBe(bytes)
    }
    expect(bytesToGiBInput(10_000_000_000)).toBe('9.31322574615478515625')
    expect(blockingGiBToBytes('10')).toBe(10_737_418_240)
    expect(blockingGiBToBytes('1.5')).toBe(1_610_612_736)
  })
  it('never infers support from role or version alone', () => {
    const unknown = { role: 'agent', version: '999', metadata: {} } as ApiNode
    expect(nodeCacheCapability(unknown)).toBe('unknown')
    for (const role of ['agent', 'proxy']) {
      const old = { role, metadata: { inventory: { capabilities: [] }, perf_settings: { kopiaCacheMb: 4096 } } } as unknown as ApiNode
      expect(nodeCacheCapability(old)).toBe('legacy'); expect(nodeUsesDemandCache(old)).toBe(false)
      expect(old.metadata?.perf_settings).toEqual({ kopiaCacheMb: 4096 })
    }
  })
  it('keeps demand-driven nodes supported when a heartbeat omits capabilities', () => {
    const node = { role: 'agent', metadata: { inventory: { capabilities: [], cache_status: { mode: 'demand_driven', application_state: 'applied' } } } } as unknown as ApiNode
    expect(nodeCacheCapability(node)).toBe('supported')
    expect(nodeUsesDemandCache(node)).toBe(true)
  })

  it('requires confirmed applied new mode before hiding legacy editors', () => {
    const node = { role: 'proxy', metadata: { inventory: { capabilities: [CACHE_CAPABILITY] }, cache_status: { mode: 'demand_driven', application_state: 'pending' } } } as unknown as ApiNode
    expect(nodeCacheCapability(node)).toBe('supported'); expect(nodeUsesDemandCache(node)).toBe(false)
    const report = node.metadata?.cache_status as { application_state: string }
    report.application_state = 'applied'; expect(nodeUsesDemandCache(node)).toBe(true)
  })
})


describe('cache validation error localization', () => {
  it.each([['en', en], ['zh-hans', zh], ['es', es]] as const)('localizes protected targets and real low-space failures in %s', (_locale, catalog) => {
    const translate = (key: string) => catalog.nodeCache.errors[key.slice('nodeCache.errors.'.length) as keyof typeof en.nodeCache.errors]
    expect(localizeCacheError('destination overlaps protected installation, repository or log data', translate)).toBe(catalog.nodeCache.errors.protectedPath)
    expect(localizeCacheError('destination available bytes 43938648064 are below blocking threshold 53687091200', translate)).toBe(catalog.nodeCache.errors.insufficientSpace)
    expect(localizeCacheError('destination parent must be an accessible existing directory: permission denied', translate)).toBe(catalog.nodeCache.errors.parentUnavailable)
    expect(localizeCacheError('old and new cache roots must be distinct and not nested', translate)).toBe(catalog.nodeCache.errors.sameOrNested)
    expect(localizeCacheError('Unexpected diagnostic with useful context', translate)).toBe('Unexpected diagnostic with useful context')
    expect(localizeCacheError('remove C:\\cache: The process cannot access the file because it is being used by another process.', translate)).toBe(catalog.nodeCache.errors.directoryInUse)
    expect(localizeCacheError('', translate)).toBe('')
  })
})
