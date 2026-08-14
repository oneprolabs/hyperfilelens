import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/styles/list-page-ui.css'), 'utf8')
const sourceStyles = readFileSync(resolve(process.cwd(), 'src/styles/source-list-ui.css'), 'utf8')
const tableStyles = readFileSync(resolve(process.cwd(), 'src/styles/element-plus-table.css'), 'utf8')
const tablePanel = readFileSync(resolve(process.cwd(), 'src/components/HflTablePanel.vue'), 'utf8')
const nodesPage = readFileSync(resolve(process.cwd(), 'src/pages/node/Nodes.vue'), 'utf8')
const dataGatewaysPage = readFileSync(resolve(process.cwd(), 'src/pages/insight/InsightDataGateways.vue'), 'utf8')
const dataProtectionPage = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

describe('responsive list toolbar controls', () => {
  it('leaves text input line height to Element Plus while preserving control height', () => {
    const inputInnerRule = tableStyles.match(/(?:^|\n)\.el-input__inner\s*{([^}]*)}/s)?.[1]

    expect(inputInnerRule).toContain('height: 34px !important')
    expect(inputInnerRule).not.toMatch(/line-height:/)
  })

  it('keeps nested search field selects at their configured width', () => {
    expect(styles).toContain('.hfl-list-search-group .el-input-group__prepend .el-select')
    expect(styles).toMatch(/\.hfl-list-search-group \.el-input-group__prepend \.el-select\s*{[^}]*width:\s*140px;/s)
    expect(styles).not.toMatch(/\.hfl-list-toolbar \.el-select\s*,/)
    expect(styles).toContain('.hfl-list-toolbar > .el-select,')
    expect(styles).toContain('.hfl-list-toolbar__right > .el-select,')
  })

  it('keeps mobile search controls beside their utility actions', () => {
    expect(styles).toContain('.hfl-list-toolbar__right--mobile-split')
    expect(styles).toContain('grid-template-columns: minmax(0, 1fr) auto')
    expect(styles).toContain('.hfl-list-toolbar__right--mobile-split > .hfl-list-search')
    expect(styles).toContain('.hfl-list-toolbar__right--mobile-split > .hfl-list-search ~ :not(.hfl-list-toolbar__utility)')
    expect(styles).toMatch(/\.hfl-list-toolbar__right--mobile-split > \.hfl-list-search ~ :not\(\.hfl-list-toolbar__utility\)\s*{[^}]*grid-column:\s*1 \/ -1;/s)
    expect(styles).toContain('.hfl-list-toolbar__right--mobile-split > .hfl-list-toolbar__utility')
    expect(nodesPage).toContain('hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split')
    expect(nodesPage).toContain('class="hfl-list-toolbar__utility"')
  })

  it('supports separate filters and utility actions in shared table panels', () => {
    expect(tablePanel).toContain("$slots['toolbar-utility']")
    expect(tablePanel).toContain("<slot name=\"toolbar-utility\" />")
    expect(tablePanel).toContain("'hfl-list-toolbar__right--mobile-split': $slots['toolbar-actions'] && $slots['toolbar-utility']")
    expect(tablePanel).toContain("'hfl-list-toolbar__right--solo': !$slots.toolbar && $slots['toolbar-actions']")
    expect(styles).toMatch(/\.hfl-list-toolbar__right--solo\s*{[^}]*width:\s*100%;[^}]*justify-content:\s*flex-end;/s)
    expect(tablePanel).toContain("'hfl-list-toolbar--mobile-primary-utility': $slots.toolbar && $slots['toolbar-actions'] && $slots['toolbar-utility']")
    expect(styles).toContain('.hfl-list-toolbar--mobile-primary-utility > .hfl-list-toolbar__primary')
    expect(styles).toContain('grid-template-columns: minmax(0, 1fr) auto')
    expect(styles).toContain('justify-self: end')
    expect(styles).toContain('.hfl-list-toolbar__primary > .el-dropdown .el-button')
    expect(styles).toContain('.hfl-list-toolbar--mobile-primary-utility > .hfl-list-toolbar__primary > .el-dropdown')
    expect(styles).toContain('vertical-align: top')
    expect(styles).toContain('grid-row: 2')
  })

  it('uses the shared primary and utility mobile layout for data gateways', () => {
    expect(dataGatewaysPage).toContain('hfl-list-toolbar hfl-list-toolbar--mobile-primary-utility')
    expect(dataGatewaysPage).toContain('class="hfl-list-toolbar__primary"')
    expect(dataGatewaysPage).toContain('hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split')
    expect(dataGatewaysPage).toContain('class="hfl-list-toolbar__utility"')
  })

  it('uses the shared primary and utility mobile layout for data protection', () => {
    expect(dataProtectionPage).toContain('class="hfl-list-toolbar hfl-list-toolbar--mobile-primary-utility protection-flow-toolbar"')
    expect(dataProtectionPage).toContain('class="hfl-list-toolbar__primary"')
    expect(dataProtectionPage).toContain('hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split')
    expect(dataProtectionPage).toContain('class="hfl-list-toolbar__utility"')
    expect(sourceStyles).toMatch(/@media \(max-width: 767\.98px\)[\s\S]*?\.protection-flow-toolbar\s*{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/)
    expect(sourceStyles).toMatch(/\.protection-flow-toolbar > \.hfl-list-toolbar__primary\s*{[^}]*grid-template-rows:\s*repeat\(2, minmax\(44px, auto\)\);/s)
    expect(sourceStyles).toMatch(/\.protection-flow-toolbar > \.hfl-list-toolbar__right--mobile-split > \.hfl-list-toolbar__utility\s*{[^}]*position:\s*absolute;[^}]*top:\s*52px;[^}]*right:\s*0;/s)
  })

  it('releases wide fixed data columns on phones', () => {
    expect(tableStyles).toContain('@media (max-width: 767.98px)')
    expect(tableStyles).toContain('.el-table-fixed-column--left:not(.el-table-column--selection):not(.el-table__expand-column)')
    expect(tableStyles).toContain('position: relative !important')
    expect(tableStyles).toContain('left: auto !important')
    expect(tableStyles).toContain('display: none !important')
  })

  it('uses compact table empty states on phones', () => {
    expect(tableStyles).toContain('--hfl-table-empty-min-height: 180px')
    expect(tableStyles).toContain('--hfl-table-empty-min-height: 200px')
    expect(tableStyles).toContain('--hfl-table-empty-image-size: 64px')
    expect(tableStyles).toContain('--el-empty-padding: 24px 0')
  })
})
