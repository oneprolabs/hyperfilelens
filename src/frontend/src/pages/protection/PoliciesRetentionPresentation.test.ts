import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { enProtectionPages } from '../../locales/enProtectionPages'

const policiesPage = readFileSync(resolve(process.cwd(), 'src/pages/protection/Policies.vue'), 'utf8')
const detailPage = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/PolicyDetailEditorForm.vue'),
  'utf8',
)
const backupWizardPage = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)
const detailPageStyles = readFileSync(resolve(process.cwd(), 'src/styles/detail-page-ui.css'), 'utf8')
const retentionConsumers = [
  policiesPage,
  detailPage,
  readFileSync(resolve(process.cwd(), 'src/pages/protection/components/ProtectionPolicyEditorForm.vue'), 'utf8'),
  backupWizardPage,
  readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8'),
  readFileSync(resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'), 'utf8'),
]
const zhHans = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'), 'utf8'),
).protection.policiesPage as Record<string, string>
const spanish = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../language-packs/packs/es/frontend/messages.json'), 'utf8'),
).protection.policiesPage as Record<string, string>

function contentDigest(value: string): string {
  return createHash('sha256').update(value).digest('hex')
}

describe('Backup Policies retention presentation', () => {
  it.each(retentionConsumers)('uses the same time-window retention copy', (source) => {
    expect(source).toContain('protection.policiesPage.shortDesc')
    expect(source).toContain('protection.policiesPage.midDesc')
    expect(source).toContain('protection.policiesPage.longDesc')
  })

  it.each([policiesPage, detailPage])('uses the shared latest-restore-point summary', (source) => {
    expect(source).toContain('protection.policiesPage.retentionLatestMany')
  })

  it('renders retention time-window rules as full lines without tier labels', () => {
    expect(policiesPage).toContain('policy-retention-detail-list__line--full')
    expect(detailPage).toContain('policy-detail-overview__retention-line--full')
  })

  it.each([
    [
      detailPageStyles,
      /\.create-policy-option-popper \.policy-retention-detail-list__(?:line|text) \{[\s\S]*?\n}/g,
    ],
    [
      backupWizardPage,
      /:global\(\.create-policy-option-popper \.policy-retention-detail-list__(?:line|text)\) \{[\s\S]*?\n}/g,
    ],
  ])('wraps long retention descriptions inside policy hover cards', (source, pattern) => {
    const rules = source.match(pattern)

    expect(rules).toHaveLength(2)
    for (const rule of rules ?? []) {
      expect(rule).toContain('overflow-wrap: anywhere;')
      expect(rule).toContain('white-space: normal;')
    }
  })

  it('describes the latest restore point consistently in every shipped language', () => {
    expect(enProtectionPages.policiesPage).toMatchObject({
      shortDesc: 'First {days} days · Keep the latest restore point each hour',
      midDesc: 'After day {start} through day {end} · Keep the latest restore point each day',
      longDesc: 'After day {day} through month {months} · Keep the latest restore point each month',
    })
    expect({
      shortDesc: contentDigest(zhHans.shortDesc),
      midDesc: contentDigest(zhHans.midDesc),
      longDesc: contentDigest(zhHans.longDesc),
    }).toEqual({
      shortDesc: 'f733b8a40f14434bd696045279fc5b420b74d6b94665433664dc2267a342a54a',
      midDesc: '85e3f0ca549d24c3bf27200659820a2010cf05f7b947e9a1650193e1bd356621',
      longDesc: 'f863afeb1bb454b3cbe4e4ff21f9049e8d08df24afb36ebba9d5f08806589200',
    })
    expect(spanish).toMatchObject({
      shortDesc: 'Primeros {days} días · Conservar el punto de restauración más reciente de cada hora',
      midDesc: 'Después del día {start} hasta el día {end} · Conservar el punto de restauración más reciente de cada día',
      longDesc: 'Después del día {day} hasta el mes {months} · Conservar el punto de restauración más reciente de cada mes',
    })
  })
})
