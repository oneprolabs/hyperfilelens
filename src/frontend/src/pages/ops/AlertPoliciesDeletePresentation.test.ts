import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'

const page = readFileSync(resolve(process.cwd(), 'src/pages/ops/AlertPolicies.vue'), 'utf8')
const code = page.slice(page.indexOf('function buildPolicyDangerAction('), page.indexOf('function openPolicyDangerDialog('))
const script = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const build = new Function('t', 'policyBatchSkipNotice', 'buildPolicyDangerItems', `${script};return buildPolicyDangerAction`)(
  (key: string) => key, () => '', () => [],
)

describe('Alert policy deletion warning consolidation', () => {
  it.each(['single-delete', 'batch-delete'])('uses one warning without repeated message or irreversible strip for %s', (kind) => {
    const action = build(kind, [{ id: 1, name: 'Rule' }])
    expect(action.message).toBe('')
    expect(action.irreversible).toBe('')
    expect(action.warning).toContain('DeleteWarning')
    expect(action.keyword).toBe('ops.alertsCenter.common.deleteKeyword')
    expect(action.level).toBe('high')
  })
  it.each(['batch-enable', 'batch-disable'])('preserves state-change presentation for %s', (kind) => {
    const action = build(kind, [{ id: 1, name: 'Rule' }])
    expect(action.message).not.toBe('')
    expect(action.irreversible).toBe('ops.alertsCenter.common.stateChangeIrreversible')
    expect(action.level).toBe('medium')
  })
  it('places the merged warning in the post-table default slot and preserves history information', () => {
    expect(page).toContain('class="alert-policy-delete-warning"')
    expect(page).toContain(':warning="pendingDangerAction.level === \'high\' ? \'\' : pendingDangerAction.warning"')
    expect(en.ops.alertsCenter.common.confirmDeleteWarning).toContain('cannot be undone')
    expect(en.ops.alertsCenter.common.confirmDeleteWarning).toContain('history')
    expect(en.ops.alertsCenter.common.confirmBatchDeleteWarning).toContain('cannot be undone')
  })
})
