import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const flowDialogStyles = readFileSync(
  resolve(process.cwd(), 'src/components/backupSourceFlowActionDialog.css'),
  'utf8',
)
const messageBoxStyles = readFileSync(
  resolve(process.cwd(), 'src/styles/element-plus-message-box.css'),
  'utf8',
)
const preflightHelper = readFileSync(
  resolve(process.cwd(), 'src/lib/nasDraftPreflight.ts'),
  'utf8',
)

describe('SMB UTF-8 preflight dialog styling', () => {
  it('matches the Deregister Backup Source dialog width and shell spacing', () => {
    expect(flowDialogStyles).toContain('--el-dialog-width: min(760px, calc(100vw - 32px))')
    expect(messageBoxStyles).toContain('--el-messagebox-width: min(760px, calc(100vw - 32px))')
    expect(messageBoxStyles).toContain('padding: 6px 20px 4px')
    expect(messageBoxStyles).toContain('padding: 10px 20px')
  })

  it('uses the Add NAS warning alert and Deregister dialog sections', () => {
    expect(preflightHelper).toContain("import '../components/backupSourceFlowActionDialog.css'")
    expect(preflightHelper).toContain("class: 'smb-utf8-preflight-dialog__alert'")
    expect(preflightHelper).toContain("class: 'smb-utf8-preflight-dialog__alert-index' }, '1'")
    expect(preflightHelper).toContain("class: 'smb-utf8-preflight-dialog__alert-index' }, '2'")
    expect(preflightHelper).toContain("class: 'smb-utf8-preflight-dialog__alert-index' }, '3'")
    expect(messageBoxStyles).toContain('.smb-utf8-preflight-dialog__alert.el-alert')
    expect(messageBoxStyles).toContain('background: var(--color-warning-light) !important')
    expect(messageBoxStyles).toContain('grid-template-columns: 22px minmax(0, 1fr)')
    expect(messageBoxStyles).toContain('border-radius: 999px')
    expect(preflightHelper).toContain("class: 'hfl-flow-action-dialog__section-title'")
  })
})
