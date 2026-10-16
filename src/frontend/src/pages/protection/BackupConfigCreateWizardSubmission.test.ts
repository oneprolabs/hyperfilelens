import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupConfigCreateWizard.vue'), 'utf8')
const protectionFlow = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

describe('backup configuration submission feedback', () => {
  it('keeps the confirmation preview visible while submitting', () => {
    expect(wizard).toMatch(/phase === 'form' \|\| phase === 'waiting'[\s\S]*?<slot name="form" \/>/)
    expect(wizard).toContain("showSteps && (phase === 'form' || phase === 'waiting' || bootstrapping)")
    expect(wizard).toContain('role="status"')
    expect(wizard).toContain('waitingText')
    expect(wizard).toMatch(/:loading="phase === 'waiting'"/)
    expect(wizard).not.toContain('<el-result')
    expect(wizard).not.toContain('phase === \'done\'')
  })

  it('does not show a fabricated percentage or warning treatment for normal submission', () => {
    expect(wizard).not.toMatch(/phase === 'waiting'[\s\S]{0,500}<el-progress/)
    expect(wizard).not.toContain('status="warning"')
    expect(wizard).not.toContain('<el-progress')
  })

  it('loads inside the destination form instead of replacing it with an initialization page', () => {
    const editor = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')
    expect(editor).toContain('v-if="createOpen || createBootstrapping"')
    expect(wizard).toContain('v-loading="bootstrapping"')
    expect(wizard).toContain(':inert="bootstrapping || phase === \'waiting\' ? true : undefined"')
    expect(wizard).not.toContain('create-backup-loading-pane')
    expect(protectionFlow).not.toContain('setup-dr-opening-skeleton')
  })
})
