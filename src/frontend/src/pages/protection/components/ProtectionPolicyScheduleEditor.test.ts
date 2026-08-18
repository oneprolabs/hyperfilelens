import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const editorSource = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/ProtectionPolicyEditorForm.vue'),
  'utf8',
)
const wizardSource = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)
const datePickerStyles = readFileSync(
  resolve(process.cwd(), 'src/styles/element-plus-date-picker.css'),
  'utf8',
)

describe('protection policy quick schedule editor', () => {
  it('exposes timezone, activation, cycle, weekly, monthly, and exact-time controls', () => {
    expect(editorSource).toContain('v-model="policyForm.scheduleTimezone"')
    expect(editorSource).toContain('v-model="policyForm.scheduleStartsAt"')
    expect(editorSource).toContain('format="YYYY-MM-DD HH:mm:ss"')
    expect(editorSource).toContain('value-format="YYYY-MM-DDTHH:mm:ss"')
    expect(editorSource).toContain('popper-class="policy-start-time-popper"')
    expect(editorSource).toContain(':teleported="true"')
    expect(editorSource).toContain('v-model="policyForm.quickScheduleType"')
    expect(editorSource).toContain('v-model="policyForm.scheduleWeekdays"')
    expect(editorSource).toContain('policyForm.scheduleMonthDays.includes(day)')
    expect(editorSource).toContain('v-model="policyForm.scheduleTime"')
    expect(editorSource).toContain("policyForm.scheduleMonthEnd = !policyForm.scheduleMonthEnd")
    expect(editorSource).toContain('.schedule-interval-unit {\n  width: 240px !important;')
  })

  it('keeps the nested second-precision time panel visible', () => {
    expect(datePickerStyles).toContain('.policy-start-time-popper.el-picker__popper')
    expect(datePickerStyles).toContain('z-index: 3600 !important;')
    expect(datePickerStyles).toContain('.policy-start-time-popper .el-date-picker__editor-wrap .el-time-panel')
    expect(datePickerStyles).toContain('width: 33.3333%;')
    expect(datePickerStyles).toContain('max-width: calc(100vw - 24px);')
  })

  it('uses the shared policy payload mapper in the backup wizard', () => {
    expect(wizardSource).toContain('policyFormToWritePayload(snapshot)')
    expect(wizardSource).not.toContain('function policyFormToPayload(')
    expect(wizardSource).not.toContain('`*/${Math.max(1, Number(form.simpleIntervalValue)')
  })
})
