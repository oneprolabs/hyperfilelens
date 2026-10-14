import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from './en'

function catalog(lang: string) {
  return JSON.parse(readFileSync(resolve(process.cwd(), `../../language-packs/packs/${lang}/frontend/messages.json`), 'utf8'))
}

describe('Force deletion presentation terminology', () => {
  it.each([
    ['en', en, 'Force Delete'],
    ['zh-hans', catalog('zh-hans'), catalog('zh-hans').nodesPage.proxyDeleteForceAction],
  ])('aligns labels and state without changing confirmation keywords in %s', (_lang, messages, label) => {
    expect(messages.nodesPage.proxyDeleteForceAction).toBe(label)
    expect(messages.nodesPage.proxyDeleteForceTitle).toContain(label)
    expect(messages.insight.dataGateway.deleteForceTitle).toContain(label)
    expect(messages.repositoriesPage.cleanupForceConfirm).toBe(label)
    expect(messages.repositoriesPage.cleanupForceOption).toBe(label)
    expect(messages.common.forceCleanupEnabled).toContain(label)
    expect(messages.common.gatewayForceRetryHint).toContain(label)
    expect(messages.repositoriesPage.cleanupForcePrompt).toContain('FORCE DELETE')
  })

  it('preserves source deregistration and excluded Copilot copy', () => {
    expect(en.protection.backupsPage.deleteForceLabel).toBe('Force Deregister')
    expect(en.insight.copilot.forceDeleteConfirmMessage).toContain('Force Cleanup')
  })
})
