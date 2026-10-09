import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupSources.vue'), 'utf8')

describe('backup source tag actions', () => {
  it('offers incremental bind/unbind from the selected host and NAS toolbar', () => {
    expect(page.split("openTagAction('add')").length - 1).toBe(2)
    expect(page.split("openTagAction('remove')").length - 1).toBe(2)
    expect(page).toContain('bulkUpdateSourceTags({')
    expect(page).toContain("t('protection.tags.unbindConfirm'")
    expect(page).toContain('v-model="tagUnbindOpen"')
    expect(page).toContain('@confirm="saveTagAction(true)"')
    expect(page).toContain("action === 'remove' && !confirmed")
    expect(page).not.toContain('ElMessageBox.confirm')
    expect(page).toContain(':sources="tagActionSources"')
    expect(page).toContain('hostBatchDisabled || tagSaving || busy')
    expect(page).toContain('nasBatchDisabled || tagSaving || busy')
    expect(page).toContain(':disabled="hostTagUnbindDisabled"')
    expect(page).toContain(':disabled="nasTagUnbindDisabled"')
    expect(page).toContain('canUnbindSelectedSources(selectedHostAgents.value.map(hostSelectableId), sourceTags.value)')
  })

  it('displays labels read-only, including an empty dash, without row edit links', () => {
    expect(page.split("t('protection.tags.column')").length - 1).toBe(2)
    expect(page).toContain('v-if="!(sourceTags[hostSelectableId(row)] || []).length"')
    expect(page).toContain('v-if="!(sourceTags[nasSelectableId(row)] || []).length"')
    expect(page).not.toContain('editSourceTags(')
    expect(page.match(/:show-icon="false"/g)).toHaveLength(2)
  })

  it.each([
    ['host', 'openSelectedHostMaintenance', 'onRemoveSelectedHosts'],
    ['nas', 'onTestSelectedNas', 'deleteSelectedNasRows'],
  ])('places the %s tag group after regular actions and immediately before deletion', (kind, lastRegularAction, deleteAction) => {
    const bind = page.indexOf(`:disabled="${kind}BatchDisabled || tagSaving || busy"`)
    const unbind = page.indexOf(`:disabled="${kind}TagUnbindDisabled"`, bind)
    const lastRegular = page.indexOf(`@click="${lastRegularAction}`)
    const deletion = page.indexOf(`@click="${deleteAction}`, unbind)
    expect(lastRegular).toBeGreaterThan(-1)
    expect(bind).toBeGreaterThan(lastRegular)
    expect(unbind).toBeGreaterThan(bind)
    expect(deletion).toBeGreaterThan(unbind)
    const bindStart = page.lastIndexOf('<ElDropdownItem', bind)
    const deleteStart = page.lastIndexOf('<ElDropdownItem', deletion)
    expect(page.slice(bindStart, bind)).toContain('divided')
    expect(page.slice(deleteStart, deletion)).toContain('divided')
    const between = page.slice(bindStart, deleteStart)
    expect(between.match(/<ElDropdownItem\b/g)).toHaveLength(2)
    const unbindStart = page.lastIndexOf('<ElDropdownItem', unbind)
    expect(page.slice(unbindStart, unbind)).not.toContain('divided')
  })

  it('places tag columns after primary host and NAS details', () => {
    const hostTable = page.split('ref="hostTableRef"')[1]?.split('</el-table>')[0] || ''
    const nasTable = page.split('ref="nasTableRef"')[1]?.split('</el-table>')[0] || ''
    expect(hostTable.indexOf("protection.sourceResources.colHostIp")).toBeLessThan(hostTable.indexOf("protection.tags.column"))
    expect(hostTable.indexOf("protection.sourceResources.colVersion")).toBeLessThan(hostTable.indexOf("protection.tags.column"))
    expect(hostTable.indexOf("protection.tags.column")).toBeLessThan(hostTable.indexOf("protection.sourceResources.colRegisteredAt"))
    expect(nasTable.indexOf("protection.sourceResources.colProtocol")).toBeLessThan(nasTable.indexOf("protection.tags.column"))
    expect(nasTable.indexOf("protection.sourceResources.colSourceProxy")).toBeLessThan(nasTable.indexOf("protection.tags.column"))
    expect(nasTable.indexOf("protection.tags.column")).toBeLessThan(nasTable.indexOf("protection.sourceResources.colRegisteredAt"))
  })
})
