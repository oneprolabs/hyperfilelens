import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')

describe('Create Backup Configuration option detail popovers', () => {
  it('opens the policy and file-filter option details beside the dropdown menu', () => {
    const sidePlacements = wizard.match(/placement="right-start"\s+:fallback-placements="\['left-start', 'right-end', 'left-end'\]"\s+:offset="10"/g)
    expect(sidePlacements).toHaveLength(2)
  })

  it('keeps file-filter status tags content-sized inside the detail grid', () => {
    const statusTags = wizard.match(/class="create-policy-detail-popover__status-tag"/g)
    expect(statusTags).toHaveLength(4)
    expect(wizard).toMatch(/\.create-policy-detail-popover__status-tag\s*\{[^}]*justify-self:\s*start;[^}]*width:\s*fit-content;/s)
  })

  it('keeps file-filter option hover previews compact', () => {
    const compactFilterPopovers = wizard.match(/<HflPopover[\s\S]*?class="create-policy-option__head"[\s\S]*?:width="380"/g)
    expect(compactFilterPopovers).toHaveLength(2)
  })

  it('keeps the table file-filter menu within the File Filter select width', () => {
    expect(wizard).toMatch(/:model-value="groupFilterId\(group\)"[\s\S]*?filterable\s+clearable\s+fit-input-width[\s\S]*?popper-class="create-policy-select-popper"/)
    expect(wizard).toContain('return filterCompiledRuleLines(filter).slice(0, 3)')
  })

  it('does not close option menus when policy or file-filter details are clicked', () => {
    const nonClosingDetails = wizard.match(/class="create-policy-detail-popover"\s+@pointerdown\.prevent\.stop\s+@mousedown\.prevent\.stop\s+@mouseup\.stop\s+@click\.stop/g)
    expect(nonClosingDetails).toHaveLength(4)
  })

  it('shows policy retention details on single lines in a wider popover', () => {
    expect(wizard.match(/:width="400"/g)).toHaveLength(4)
    expect(wizard.match(/popper-class="create-policy-option-popper create-policy-option-popper--policy"/g)).toHaveLength(4)
    expect(wizard).toMatch(/\.create-policy-option-popper\.create-policy-option-popper--policy\)\s*\{[^}]*max-width:\s*min\(400px,/s)
    expect(wizard).toMatch(/\.policy-retention-detail-list__line\)\s*\{[^}]*font-size:\s*12px;[^}]*white-space:\s*nowrap;/s)
  })

  it('removes option hover popovers without a stale-position exit frame when config selects close', () => {
    const immediateExitPopovers = wizard.match(/<HflPopover[\s\S]*?transition="hfl-option-popover-immediate"[\s\S]*?popper-class="create-policy-option-popper(?: create-policy-option-popper--policy)?"/g)
    expect(immediateExitPopovers).toHaveLength(4)
    expect(wizard).not.toContain(':teleported="false"')
    expect(wizard).toMatch(/function handleConfigSelectVisibleChange\(visible: boolean\)\s*\{[\s\S]*?if \(!visible\) hideOptionPopovers\(\)/)
  })
})
