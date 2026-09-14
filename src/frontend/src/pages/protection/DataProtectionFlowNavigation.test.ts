import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

describe('backup wizard flow navigation', () => {
  it('validates the first-step selection before moving and clearing it', () => {
    const start = page.indexOf('async function enterBackupConfigStep')
    const end = page.indexOf('function onGoToStep2', start)
    const handler = page.slice(start, end)
    const validation = handler.indexOf('picked.length === 0')
    const move = handler.indexOf('await syncStep2SourcesFromSelection(picked)')

    expect(start).toBeGreaterThan(-1)
    expect(validation).toBeGreaterThan(-1)
    expect(move).toBeGreaterThan(validation)
    expect(handler).not.toContain('step2PendingCount.value === 0')
  })

  it('guards the asynchronous Next transition against duplicate clicks', () => {
    expect(page).toContain('if (flowAdvancingToBackupConfig.value) return')
    expect(page).toContain(':loading="flowAdvancingToBackupConfig"')
  })

  it('closes the active More Actions dropdown before changing steps', () => {
    expect(page).toContain('const flowMoreActionsDropdownRef = ref<DropdownInstance | null>(null)')
    expect(page).toMatch(/function closeFlowMoreActions\(\) \{\s*flowMoreActionsDropdownRef\.value\?\.handleClose\(\)\s*moreActionsOpen\.value = false/)
    expect(page.match(/ref="flowMoreActionsDropdownRef"/g)).toHaveLength(3)
    expect(page).toMatch(/watch\(flowMainStep, \(step\) => \{\s*closeFlowMoreActions\(\)/)
  })
})
