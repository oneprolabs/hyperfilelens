import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'

function source(): string {
  return readFileSync(
    resolve(process.cwd(), 'src/pages/insight/InsightKnowledgeSourceDetailDrawer.vue'),
    'utf8',
  )
}

describe('Knowledge Source detail drawer', () => {
  const page = source()

  it('uses Basic Information tab with General / Source / Conversion / Technical sections', () => {
    expect(en.insight.kb.tabSource).toBe('Basic Information')
    expect(en.insight.kb.sectionGeneral).toBe('General')
    expect(en.insight.kb.sectionSource).toBe('Source')
    expect(en.insight.kb.sectionConversion).toBe('Document Conversion')
    expect(en.insight.kb.sectionTechnical).toBe('Technical Details')
    expect(page).toContain("t('insight.kb.sectionGeneral')")
    expect(page).toContain("t('insight.kb.sectionSource')")
    expect(page).toContain("t('insight.kb.sectionConversion')")
    expect(page).toContain("t('insight.kb.sectionTechnical')")
    expect(page).toContain('hfl-detail-section__title')
    expect(page).not.toContain('ks-kb-detail-card__indicator')
  })

  it('aligns detail labels with list columns', () => {
    expect(page).toContain("t('insight.kb.colName')")
    expect(page).toContain("t('insight.kb.colSourcePath')")
    expect(page).toContain("t('insight.kb.colSourceType')")
    expect(page).toContain("t('insight.kb.colGateway')")
    expect(page).toContain("t('insight.kb.colRetrieval')")
    expect(page).toContain("t('insight.kb.colLearnStatus')")
    expect(page).toContain("t('insight.kb.fieldSnapshotMode')")
    expect(en.insight.kb.fieldSourcePath).toBe('Source Path')
    expect(en.insight.kb.fieldWorkspace).toBe('Workspace Path')
  })

  it('links Organization without showing org key', () => {
    expect(page).toContain('organizationId')
    expect(page).toContain('`/platform-ops/orgs/${organizationId(activeRow)}`')
    expect(page).toContain("row?.organization_name?.trim() || ''")
  })

  it('renders Retrieval Enhancement as compact panels without duplicated lead walls', () => {
    expect(page).toContain('ks-retrieval-panel')
    expect(page).toContain('compact')
    expect(page).toContain('ks-retrieval-panels__lead')
    expect(page).not.toContain(':show-lead="true"')
  })

  it('shows conversion Result status and Failed Files as mono paths', () => {
    expect(en.insight.kb.fieldConversionResult).toBe('Result')
    expect(en.insight.kb.fieldConversionFailedFiles).toBe('Failed Files')
    expect(en.insight.kb.fieldConversionWarnings).toBe('Warnings')
    expect(page).toContain('conversionResultStatus')
    expect(page).toContain('conversionFailedFiles')
    expect(page).toContain("t('insight.kb.fieldConversionFailedFiles')")
    expect(page).toContain('hfl-detail-row__value--mono')
  })
})
