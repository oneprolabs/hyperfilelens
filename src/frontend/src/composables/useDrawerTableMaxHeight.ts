import { nextTick, onMounted, onUnmounted, ref, watch, type ComponentPublicInstance } from 'vue'

type MeasurableElement = HTMLElement | ComponentPublicInstance<{ $el: HTMLElement }>

/**
 * Computes a max-height value for a table inside a drawer tab so the
 * table body scrolls internally without making the drawer pane scroll.
 *
 * Mirrors the HflTablePanel ResizeObserver pattern: measures the
 * distance from the container's top to the bottom of the drawer body
 * viewport (the `.el-drawer__body`), then subtracts the offset for
 * section headings, padding, and scrollbar reserve.
 *
 * @param offset - Pixels to subtract for chrome above the table
 *   (section heading, bottom padding, scrollbar reserve).
 */
export function useDrawerTableMaxHeight(offset = 16) {
  const tableMaxHeight = ref(400)
  const containerRef = ref<MeasurableElement | null>(null)

  let resizeObserver: ResizeObserver | null = null
  let observedElement: HTMLElement | null = null
  let observedViewport: HTMLElement | null = null

  function resolveElement(value: MeasurableElement | null) {
    if (value instanceof HTMLElement) return value
    return value?.$el instanceof HTMLElement ? value.$el : null
  }

  function update() {
    const el = resolveElement(containerRef.value)
    if (!el) return

    const taskDrawer = el.closest<HTMLElement>('.hfl-task-drawer')
    const viewport = taskDrawer?.querySelector<HTMLElement>(':scope > .el-drawer__body')
      ?? el.closest<HTMLElement>('.el-tab-pane, .hfl-detail-drawer__body, .repo-detail-drawer__body, .hfl-task-drawer__body')
    if (!viewport) return

    const viewportRect = viewport.getBoundingClientRect()
    const tableRect = el.getBoundingClientRect()
    let trailingHeight = 0

    for (let sibling = el.nextElementSibling; sibling; sibling = sibling.nextElementSibling) {
      if (!(sibling instanceof HTMLElement)) continue
      const style = window.getComputedStyle(sibling)
      if (style.display === 'none' || style.position === 'absolute' || style.position === 'fixed') continue
      const rect = sibling.getBoundingClientRect()
      trailingHeight += rect.height + Number.parseFloat(style.marginTop || '0') + Number.parseFloat(style.marginBottom || '0')
    }

    const next = Math.max(160, Math.floor(viewportRect.bottom - tableRect.top - trailingHeight - offset))

    if (tableMaxHeight.value !== next) {
      tableMaxHeight.value = next
    }
  }

  function observe(value: MeasurableElement | null) {
    resizeObserver?.disconnect()
    observedViewport?.removeEventListener('scroll', update)
    observedElement = resolveElement(value)
    const taskDrawer = observedElement?.closest<HTMLElement>('.hfl-task-drawer')
    observedViewport = taskDrawer?.querySelector<HTMLElement>(':scope > .el-drawer__body')
      ?? observedElement?.closest<HTMLElement>('.el-tab-pane, .hfl-detail-drawer__body, .repo-detail-drawer__body, .hfl-task-drawer__body')
      ?? null
    if (observedElement) resizeObserver?.observe(observedElement)
    if (observedViewport) {
      resizeObserver?.observe(observedViewport)
      observedViewport.addEventListener('scroll', update, { passive: true })
    }
    for (const child of observedElement?.parentElement?.children ?? []) {
      if (child instanceof HTMLElement) resizeObserver?.observe(child)
    }
    void nextTick(update)
    requestAnimationFrame(update)
  }

  const stopWatching = watch(containerRef, observe, { flush: 'post' })

  onMounted(() => {
    resizeObserver = new ResizeObserver(() => update())
    observe(containerRef.value)
    window.addEventListener('resize', update)
    window.visualViewport?.addEventListener('resize', update)
  })

  onUnmounted(() => {
    stopWatching()
    resizeObserver?.disconnect()
    observedViewport?.removeEventListener('scroll', update)
    window.removeEventListener('resize', update)
    window.visualViewport?.removeEventListener('resize', update)
  })

  return { tableMaxHeight, containerRef }
}
