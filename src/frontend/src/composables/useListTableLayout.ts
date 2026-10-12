import { nextTick, onMounted, onUnmounted, onUpdated, ref, type Ref } from 'vue'
import type { TableInstance } from 'element-plus'
import { layoutElTable, syncTableHeaderScroll } from '../lib/tableScrollSync'
import { listTableMaxHeight } from '../lib/listTableHeight'

function syncTableElementScroll(tableRef: TableInstance | null, event?: Event) {
  const el = tableRef?.$el as HTMLElement | undefined
  if (el instanceof HTMLElement) {
    const header = el.querySelector<HTMLElement>('.el-table__header-wrapper')
    const footer = el.querySelector<HTMLElement>('.el-table__footer-wrapper')
    const wrap = el.querySelector<HTMLElement>('.el-scrollbar__wrap')
    const body = el.querySelector<HTMLElement>('.el-table__body-wrapper')
    const target = event?.target
    const scrollLeft =
      target instanceof HTMLElement && Number.isFinite(target.scrollLeft)
        ? target.scrollLeft
        : wrap?.scrollLeft ?? body?.scrollLeft ?? 0

    if (header) header.scrollLeft = scrollLeft
    if (footer) footer.scrollLeft = scrollLeft
  }
  syncTableHeaderScroll(tableRef as Parameters<typeof syncTableHeaderScroll>[0], event)
}

const DEFAULT_FOOTER_RESERVE = 48

export function useListTableLayout(
  tableRef: Ref<TableInstance | null>,
  blockRef: Ref<HTMLElement | null>,
  options?: { footerReserve?: number },
) {
  const tableMaxHeight = ref(400)
  const footerReserve = options?.footerReserve ?? DEFAULT_FOOTER_RESERVE

  function layoutTable() {
    nextTick(() => {
      layoutElTable(tableRef.value as Parameters<typeof layoutElTable>[0])
    })
  }

  function updateTableMaxHeight() {
    const block = blockRef.value
    if (!block) return

    const panel = block.closest('.hfl-list-panel') as HTMLElement | null
    const footer = panel?.querySelector<HTMLElement>('.hfl-list-footer')
    if (observedFooter !== (footer ?? null)) {
      if (observedFooter) resizeObserver?.unobserve(observedFooter)
      observedFooter = footer ?? null
      if (observedFooter) resizeObserver?.observe(observedFooter)
    }
    const next = listTableMaxHeight(block, footer, footerReserve)

    if (tableMaxHeight.value !== next) {
      tableMaxHeight.value = next
      layoutTable()
    }
  }

  function handleTableScroll(event?: Event) {
    syncTableElementScroll(tableRef.value, event)
  }

  let resizeObserver: ResizeObserver | null = null
  let observedFooter: HTMLElement | null = null

  onUpdated(updateTableMaxHeight)

  onMounted(() => {
    resizeObserver = new ResizeObserver(() => updateTableMaxHeight())
    if (blockRef.value) resizeObserver.observe(blockRef.value)
    const pageBody = blockRef.value?.closest('.page-body')
    if (pageBody) resizeObserver.observe(pageBody)
    window.addEventListener('resize', updateTableMaxHeight)
    requestAnimationFrame(updateTableMaxHeight)
  })

  onUnmounted(() => {
    resizeObserver?.disconnect()
    window.removeEventListener('resize', updateTableMaxHeight)
  })

  return {
    tableMaxHeight,
    layoutTable,
    updateTableMaxHeight,
    handleTableScroll,
  }
}
