import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'

const page = readFileSync(resolve(process.cwd(), 'src/pages/ops/NotificationChannels.vue'), 'utf8')
const code = page.slice(page.indexOf('function buildPendingBatch('), page.indexOf('function buildPendingBatchPlaceholder('))
const script = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const build = new Function('t', 'buildBatchChannelItems', 'buildBatchPolicyImpactItems', 'channelBatchSkipNotice', `${script};return buildPendingBatch`)(
  (key: string) => key, () => [], () => [], () => '',
)

describe('Notification channel deletion risk consolidation', () => {
  it.each([false, true])('uses a single irreversible warning with linked rules=%s', (linked) => {
    const result = build('delete', [{ policies: linked ? [{ id: 1 }] : [] }])
    expect(result.warning).toBe(linked ? 'ops.notification.deleteMergedLinkedWarning' : 'ops.notification.deleteMergedWarning')
    expect(result.irreversible).toBe('')
    expect(result.keyword).toBe('ops.notification.deleteChannelKeyword')
    expect(result.level).toBe('high')
  })
  it.each(['enable', 'disable'])('preserves state-change warning and hint for %s', (kind) => {
    const result = build(kind, [{ policies: [{ id: 1 }] }])
    expect(result.irreversible).toBe('ops.notification.batchIrreversible')
    expect(result.warning).toContain(kind === 'enable' ? 'batchEnableWarning' : 'batchDisableWarning')
    expect(result.level).toBe('medium')
  })
  it('retains association safety gates and displays deletion warnings in the post-table slot', () => {
    expect(page).toContain(':pending="batchAssociationLoading"')
    expect(page).toContain(':error-text="batchAssociationError')
    expect(page).toContain(':pending="deleteDialogState.associationStatus === \'checking\'"')
    expect(page).toContain(':warning="pendingBatch.kind === \'delete\' ? \'\' : pendingBatch.warning"')
    expect(page).not.toContain(':irreversible-hint="deleteDialogIrreversible"')
    expect(page).toContain(':title="deleteDialogWarning"')
    expect(page).toContain('class="notification-delete-warning"')
    expect(en.ops.notification.deleteMergedWarning).toContain('historical delivery records')
    expect(en.ops.notification.deleteMergedLinkedWarning).toContain('through them')
  })
})
