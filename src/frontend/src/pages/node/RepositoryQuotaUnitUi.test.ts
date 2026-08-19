import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = (name: string) => readFileSync(resolve(process.cwd(), `src/pages/node/${name}`), 'utf8')
const quotaStyles = readFileSync(resolve(process.cwd(), 'src/styles/resource-add.css'), 'utf8')

describe('repository quota unit UI coverage', () => {
  it.each([
    'AddS3Repo.vue',
    'AddNasRepository.vue',
    'AddProxyFsRepository.vue',
    'EditS3Repo.vue',
    'EditProxyFsRepo.vue',
    'RepairNasRepository.vue',
  ])('provides a persisted unit selector in %s', (file) => {
    const source = page(file)
    expect(source).toContain('REPOSITORY_QUOTA_UNITS')
    expect(source).toContain('v-model="quotaUnit"')
    expect(source).toContain('repository-quota-split-input')
    expect(source).toContain('quota_unit: quotaUnit.value')
    expect(source).toContain(':precision="0"')
  })

  it.each(['AddS3Repo.vue', 'AddNasRepository.vue', 'AddProxyFsRepository.vue'])('frames the Storage Limit control on %s without changing edit forms', (file) => {
    expect(page(file)).toContain('repository-quota-split-input--add')
  })

  it.each(['EditS3Repo.vue', 'EditProxyFsRepo.vue', 'RepairNasRepository.vue'])('rounds the Storage Limit stepper controls on %s', (file) => {
    expect(page(file)).toContain('repository-quota-split-input--edit')
  })

  it('uses the shared formatter for all repository detail variants', () => {
    const source = page('Repositories.vue')
    expect(source).toContain('repositoryQuotaDisplay(row.config)')
    expect(source).toContain("quota_unit: configString(config, 'quota_unit')")
  })

  it('restores the standard background only on the Storage Limit value input', () => {
    expect(quotaStyles).toContain('> .repository-quota-number__input.el-input-number')
    expect(quotaStyles).toContain('> .hfl-detail-form-input__num.el-input-number')
    expect(quotaStyles).toContain('background: var(--el-fill-color-blank) !important;')
    expect(quotaStyles).not.toContain(
      '.resource-add-fullscreen .repository-quota-split-input :is(.repository-quota-number__unit, .hfl-detail-form-input__unit)',
    )
  })

  it('keeps add-form value and unit fields separate and rounded', () => {
    expect(quotaStyles).toContain('.repository-quota-split-input--add')
    expect(quotaStyles).toContain('gap: 8px;')
    expect(quotaStyles).toContain('border: 0 !important;')
    expect(quotaStyles).toContain('border-radius: var(--el-border-radius-base) !important;')
  })

  it('restores matching corners on edit quota stepper buttons', () => {
    expect(quotaStyles).toContain('.repository-quota-split-input--edit')
    expect(quotaStyles).toContain('border-top-right-radius: var(--el-border-radius-base) !important;')
    expect(quotaStyles).toContain('border-bottom-right-radius: var(--el-border-radius-base) !important;')
  })
})
