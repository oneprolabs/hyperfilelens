import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'

const page = readFileSync(resolve(process.cwd(), 'src/pages/node/AddNasRepository.vue'), 'utf8')
const zhHans = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
)) as {
  addNasRepo: Record<string, string>
  repairNasRepo: Record<string, string>
  protection: {
    sourceResources: Record<string, string>
  }
}

describe('Add NAS Repository localization', () => {
  it('routes user-visible NAS form copy through the locale catalog', () => {
    for (const literal of [
      'Save configuration',
      'Submit and initialize',
      'If SMB auto-negotiation fails',
      'Different NAS devices, SMB servers',
      'SMB user used to access the shared directory',
      'Password for the shared directory',
      'Use CORP or WORKGROUP for domain environments',
      'Display name used in repository lists and backup configs',
    ]) {
      expect(page).not.toContain(literal)
    }
  })

  it('provides English source copy and Simplified Chinese translations', () => {
    const keys = [
      'btnSaveConfiguration',
      'btnSubmitInitialize',
      'hintSmbMountOptions',
      'hintSmbMountOptionsExamples',
      'hintSmbUsername',
      'hintSmbPassword',
      'hintSmbDomain',
      'hintRepositoryName',
    ] as const

    for (const key of keys) {
      expect(en.addNasRepo[key]).toEqual(expect.any(String))
      expect(en.addNasRepo[key].trim()).not.toBe('')
      expect(zhHans.addNasRepo[key]).toEqual(expect.any(String))
      expect(zhHans.addNasRepo[key].trim()).not.toBe('')
      expect(zhHans.addNasRepo[key]).not.toBe(en.addNasRepo[key])
    }
  })

  it('uses NAS sharing terminology for the SMB share name', () => {
    const shareName = zhHans.protection.sourceResources.colNasShareName
    expect(shareName).toEqual(expect.any(String))
    expect(zhHans.addNasRepo.fieldSmbShare).toBe(shareName)
    expect(zhHans.repairNasRepo.labelShareName).toBe(shareName)
  })

  it('keeps NAS input examples technically usable', () => {
    expect(zhHans.addNasRepo.phSmbShare).toBe('data')
    expect(zhHans.protection.sourceResources.nasPhSmbShare).toBe('data')
    expect(zhHans.protection.sourceResources.nasPhSmbUsername).toBe('admin')
    expect(zhHans.protection.sourceResources.nasPhMountOptionsSmb)
      .toBe('vers=3.0,iocharset=utf8,uid=1000,gid=1000')
  })
})
