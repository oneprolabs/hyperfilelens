/** The page's border/padding are not available to the table or its footer. */
export function listTableMaxHeight(
  tableRegion: HTMLElement,
  footer: HTMLElement | null | undefined,
  footerReserve = 0,
) {
  const page = tableRegion.closest<HTMLElement>('.page-body')
  const viewportBottom = window.visualViewport?.height ?? window.innerHeight
  const pageStyle = page ? window.getComputedStyle(page) : null
  const bottomInset = pageStyle
    ? (Number.parseFloat(pageStyle.paddingBottom) || 0) + (Number.parseFloat(pageStyle.borderBottomWidth) || 0)
    : 0
  const bottom = Math.min(page ? page.getBoundingClientRect().bottom - bottomInset : viewportBottom, viewportBottom)
  const footerHeight = footer?.getBoundingClientRect().height ?? footerReserve
  // max-height is a cap, not a minimum. A 280px floor can overflow short pages.
  return Math.max(1, Math.floor(bottom - tableRegion.getBoundingClientRect().top - footerHeight))
}
