import { describe, expect, it } from 'vitest'
import { en } from './en'
import { enProtectionPages } from './enProtectionPages'

describe('source deregistration copy', () => {
  it('uses the concise action name at contextual source action entry points', () => {
    expect(enProtectionPages.sourceResources.deleteBtn).toBe('Deregister')
    expect(enProtectionPages.backupsPage.flowActionDelete).toBe('Deregister')
    expect(enProtectionPages.backupsPage.btnConfirmUnregisterSource).toBe('Deregister Backup Source')
  })

  it('uses deregistration terminology for the related task display', () => {
    expect(en.ops.task.taskType.source_unregister).toBe('Source Deregistration')
    expect(en.ops.task.triggeredBySourceUnregister).toBe('Triggered by Source Deregistration')
  })

  it('uses the deregistration term for destructive confirmation', () => {
    expect(enProtectionPages.backupsPage.deleteConfirmPlaceholder).toBe('DEREGISTER')
    expect(enProtectionPages.backupsPage.deleteConfirmTypeKeyword).toContain('type DEREGISTER below')
    expect(enProtectionPages.backupsPage.deleteForceLabel).toBe('Force Deregister')
    expect(enProtectionPages.backupsPage.deleteForceConfirmTypeKeyword)
      .toBe('To confirm Force Deregister, type FORCE DEREGISTER below.')
  })
})
