import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

const styles = source('src/styles/detail-page-ui.css')
const drawerHeight = source('src/composables/useDrawerTableMaxHeight.ts')

describe('detail drawer scrolling', () => {
  it('keeps drawer chrome fixed and scrolls only the active tab pane', () => {
    expect(styles).toContain('.hfl-detail-drawer.el-drawer > .el-drawer__body')
    expect(styles).toContain('.hfl-task-drawer.el-drawer > .el-drawer__body')
    expect(styles).toContain('background: var(--el-bg-color)')
    expect(styles).toContain('.hfl-detail-tabs.el-tabs > .el-tabs__content')
    expect(styles).toContain('.hfl-detail-tabs.el-tabs > .el-tabs__content > .el-tab-pane')
    expect(styles).toContain('overflow: hidden auto')

    const tabHeaderRule = styles.match(
      /\.hfl-detail-tabs\.el-tabs > \.el-tabs__header,[\s\S]*?\n}/,
    )?.[0]
    expect(tabHeaderRule).toContain('flex: 0 0 auto')
    expect(tabHeaderRule).not.toContain('position: sticky')
  })

  it('lets task summaries scroll away before pinning the tab header', () => {
    expect(styles).toContain('.hfl-task-drawer.el-drawer > .el-drawer__body')
    expect(styles).toContain('.hfl-task-drawer .hfl-task-drawer__body')
    expect(styles).toContain('padding-top: 0 !important')
    expect(styles).toContain('padding-top: var(--el-drawer-padding-primary) !important')
    expect(styles).toContain('.hfl-task-drawer .hfl-detail-tabs.el-tabs > .el-tabs__header')
    expect(styles).toContain('position: sticky')
    expect(styles).toContain('padding-bottom: 12px')
    expect(styles).toContain('isolation: isolate')
    expect(styles).toContain('z-index: 100')
    expect(styles).toContain('.hfl-task-drawer .hfl-detail-tabs.el-tabs > .el-tabs__content')
    expect(styles).toContain('.hfl-task-drawer .hfl-detail-tabs.el-tabs > .el-tabs__content > .el-tab-pane')
  })

  it('measures table height from the actual drawer content viewport', () => {
    expect(drawerHeight).toContain("'.el-tab-pane, .hfl-detail-drawer__body, .repo-detail-drawer__body, .hfl-task-drawer__body'")
    expect(drawerHeight).toContain("taskDrawer?.querySelector<HTMLElement>(':scope > .el-drawer__body')")
    expect(drawerHeight).toContain('viewportRect.bottom - tableRect.top - trailingHeight - offset')
    expect(drawerHeight).toContain('el.nextElementSibling')
    expect(drawerHeight).toContain('watch(containerRef, observe')
    expect(drawerHeight).toContain('ResizeObserver')
    expect(drawerHeight).toContain("observedViewport.addEventListener('scroll', update")
  })

  it('does not use viewport offset constants for tables inside detail drawers', () => {
    const drawerTableSources = [
      'src/components/ProxyBoundNasSourcesPanel.vue',
      'src/components/ProxyStorageRepositoriesPanel.vue',
      'src/pages/node/Repositories.vue',
      'src/pages/ops/NotificationChannels.vue',
      'src/pages/ops/Tasks.vue',
      'src/pages/protection/DataProtection.vue',
      'src/pages/protection/Policies.vue',
      'src/pages/protection/BackupDetail.vue',
      'src/pages/protection/components/BackupConfigDetailPanel.vue',
      'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue',
      'src/pages/protection/components/TaskDetailDrawer.vue',
    ]

    for (const path of drawerTableSources) {
      const contents = source(path)
      expect(contents).not.toMatch(/max-height=["{:`]+calc\(var\(--app-viewport-height\) - (?:170|220|250|260|265|320)px\)/)
    }
  })

  it('covers every table discovered in detail drawer templates and child panels', () => {
    const expectedBindings = new Map([
      ['src/pages/ops/NotificationChannels.vue', ['policyTableMaxHeight', 'deliveryTableMaxHeight']],
      ['src/pages/protection/DataProtection.vue', ['runningRestoreTableMaxHeight', 'restoreHistoryTableMaxHeight']],
      ['src/pages/protection/components/BackupConfigDetailPanel.vue', ['tableMaxHeight']],
      ['src/pages/protection/components/FlowBackupSourceDetailDrawer.vue', [
        'snapshotTableMaxHeight',
        'restoreTableMaxHeight',
        'sourceTasksTableMaxHeight',
        'taskResourceTableMaxHeight',
      ]],
    ])

    for (const [path, bindings] of expectedBindings) {
      const contents = source(path)
      for (const binding of bindings) {
        expect(contents).toContain(`:max-height="${binding}"`)
      }
    }
  })
})
