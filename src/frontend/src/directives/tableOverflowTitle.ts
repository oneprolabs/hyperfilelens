import type { App } from 'vue'

type OverflowTitleState = {
  active: HTMLElement | null
  pending: HTMLElement | null
  pendingTimer: number | null
  hideTimer: number | null
  contentObserver: MutationObserver | null
  tooltipHovered: boolean
  resolveTarget: (eventTarget: EventTarget | null) => HTMLElement | null
  titleForTarget: (target: HTMLElement) => string
  onMouseOver: (event: MouseEvent) => void
  onMouseOut: (event: MouseEvent) => void
  onFocusIn: (event: FocusEvent) => void
  onFocusOut: (event: FocusEvent) => void
  onScroll: () => void
  onResize: () => void
  onColumnResize: () => void
}

const stateByElement = new WeakMap<HTMLElement, OverflowTitleState>()
const MIN_TITLE_LENGTH = 2
const TOOLTIP_ID = 'hfl-table-overflow-tooltip'
const TOOLTIP_GAP = 8
const TOOLTIP_SHOW_AFTER_MS = 300
const TOOLTIP_HIDE_AFTER_MS = 150
const VIEWPORT_PADDING = 8
const COLUMN_RESIZED_EVENT = 'hfl-table-column-resized'

let autoInstallStarted = false
let activeState: OverflowTitleState | null = null
let tooltipElement: HTMLDivElement | null = null

function ensureTooltip() {
  if (tooltipElement) return tooltipElement

  const tooltip = document.createElement('div')
  tooltip.id = TOOLTIP_ID
  tooltip.className = 'hfl-table-overflow-tooltip'
  tooltip.setAttribute('role', 'tooltip')
  tooltip.style.display = 'none'
  tooltip.addEventListener('mouseenter', () => {
    if (activeState) {
      activeState.tooltipHovered = true
      clearPendingHide(activeState)
    }
  })
  tooltip.addEventListener('mouseleave', () => {
    if (activeState) {
      activeState.tooltipHovered = false
      scheduleTooltipHide(activeState)
    }
  })
  document.body.appendChild(tooltip)
  tooltipElement = tooltip
  return tooltip
}

function isCurrentTableElement(el: HTMLElement, node: Element | null) {
  return node?.closest('.el-table') === el
}

function isIgnoredCell(cell: HTMLElement) {
  const tableCell = cell.closest<HTMLElement>('td.el-table__cell, th.el-table__cell')
  return Boolean(
    tableCell?.classList.contains('el-table-column--selection') ||
      tableCell?.classList.contains('el-table__expand-column'),
  )
}

function textFromElement(element: HTMLElement) {
  return element.innerText
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
}

function hasMeaningfulText(element: HTMLElement) {
  const text = textFromElement(element)
  return text.length >= MIN_TITLE_LENGTH && text !== '—'
}

function hasGeometricOverflow(element: HTMLElement) {
  const elementRect = element.getBoundingClientRect()
  if (elementRect.width <= 0 || elementRect.height <= 0) return false

  const range = document.createRange()
  range.selectNodeContents(element)
  const contentRect = range.getBoundingClientRect()
  const style = window.getComputedStyle(element)
  const borderLeft = Number.parseFloat(style.borderLeftWidth) || 0
  const borderRight = Number.parseFloat(style.borderRightWidth) || 0
  const borderTop = Number.parseFloat(style.borderTopWidth) || 0
  const borderBottom = Number.parseFloat(style.borderBottomWidth) || 0

  return (
    contentRect.left < elementRect.left + borderLeft ||
    contentRect.right > elementRect.right - borderRight ||
    contentRect.top < elementRect.top + borderTop ||
    contentRect.bottom > elementRect.bottom - borderBottom
  )
}

function isOverflowing(element: HTMLElement) {
  // Even a one-pixel overflow makes CSS render an ellipsis, so the tooltip
  // must use the same strict boundary instead of hiding these edge cases.
  // DOM scroll dimensions are integer-rounded, while layout and ellipsis
  // painting use subpixels, so compare rendered geometry as a fallback.
  return (
    element.scrollWidth > element.clientWidth ||
    element.scrollHeight > element.clientHeight ||
    hasGeometricOverflow(element)
  )
}

function overflowCandidates(cell: HTMLElement) {
  return Array.from(cell.querySelectorAll('*'))
    .filter((item): item is HTMLElement => item instanceof HTMLElement)
    .filter((item) => !item.closest('.hfl-table-no-tooltip'))
    .filter((item) => hasMeaningfulText(item))
    .filter((item) => {
      const style = window.getComputedStyle(item)
      return (
        style.overflow === 'hidden' ||
        style.textOverflow === 'ellipsis' ||
        style.whiteSpace === 'nowrap' ||
        item.classList.contains('hfl-table-name-link') ||
        item.classList.contains('hfl-table-cell-mono') ||
        item.classList.contains('hfl-table-cell-time') ||
        Array.from(item.classList).some((className) => className.includes('__path') || className.includes('__name'))
      )
    })
}

function titleTextForCell(cell: HTMLElement) {
  const explicitTargets = Array.from(cell.querySelectorAll<HTMLElement>('[data-table-overflow-title]'))
  const alwaysExplicitTitle = explicitTargets.find((target) => {
    return target.hasAttribute('data-table-overflow-title-always')
      && Boolean(target.dataset.tableOverflowTitle?.trim())
  })?.dataset.tableOverflowTitle?.trim()
  if (alwaysExplicitTitle) return alwaysExplicitTitle

  const overflowingExplicitTarget = explicitTargets.find((target) => {
    return Boolean(target.dataset.tableOverflowTitle?.trim()) && isOverflowing(target)
  })
  const explicitTitle = overflowingExplicitTarget?.dataset.tableOverflowTitle?.trim()
  if (explicitTitle) return explicitTitle

  // If the table cell itself is clipped but its explicitly titled child does
  // not report overflow independently (common with flex layouts), preserve
  // the structured title instead of falling back to all visible cell text.
  const structuredTitle = explicitTargets.find((target) => target.dataset.tableOverflowTitle?.trim())
    ?.dataset.tableOverflowTitle?.trim()
  if (structuredTitle && isOverflowing(cell)) return structuredTitle

  // Some compound cells intentionally expose only their structured detail
  // text. Do not fall back to duplicating visible status labels in a tooltip.
  if (cell.querySelector('[data-table-overflow-explicit-only]')) return ''

  const candidates = overflowCandidates(cell)
  const overflowingCandidates = candidates.filter(isOverflowing)
  const targets = overflowingCandidates.filter(
    (candidate) => !overflowingCandidates.some((other) => other !== candidate && candidate.contains(other)),
  )

  if (targets.length > 0) {
    return targets.map(textFromElement).filter(Boolean).join('\n')
  }

  return isOverflowing(cell) && hasMeaningfulText(cell) ? textFromElement(cell) : ''
}

function isNoTooltipCell(cell: HTMLElement, eventTarget: EventTarget | null) {
  if (eventTarget instanceof Element && eventTarget.closest('.hfl-table-no-tooltip')) return true
  return false
}

function findCell(el: HTMLElement, eventTarget: EventTarget | null) {
  if (!(eventTarget instanceof Element)) return null
  const cell = eventTarget.closest<HTMLElement>('.el-table__cell .cell')
  if (!cell || !isCurrentTableElement(el, cell) || isIgnoredCell(cell)) return null
  if (isNoTooltipCell(cell, eventTarget)) return null
  return cell
}

function positionTooltip(cell: HTMLElement) {
  if (!tooltipElement || tooltipElement.style.display === 'none') return

  const rect = cell.getBoundingClientRect()
  const tooltipRect = tooltipElement.getBoundingClientRect()
  const maxLeft = window.innerWidth - tooltipRect.width - VIEWPORT_PADDING
  const left = Math.max(VIEWPORT_PADDING, Math.min(rect.left, maxLeft))
  const belowTop = rect.bottom + TOOLTIP_GAP
  const aboveTop = rect.top - tooltipRect.height - TOOLTIP_GAP
  const visibleViewportBottom = (window.visualViewport?.offsetTop ?? 0) + (window.visualViewport?.height ?? window.innerHeight)
  const top =
    belowTop + tooltipRect.height <= visibleViewportBottom - VIEWPORT_PADDING
      ? belowTop
      : Math.max(VIEWPORT_PADDING, aboveTop)

  tooltipElement.style.left = `${left}px`
  tooltipElement.style.top = `${top}px`
}

function stopObservingTooltipContent(state: OverflowTitleState) {
  state.contentObserver?.disconnect()
  state.contentObserver = null
}

function observeTooltipContent(state: OverflowTitleState, cell: HTMLElement) {
  stopObservingTooltipContent(state)
  const observer = new MutationObserver(() => {
    if (state.active !== cell) return
    if (!cell.isConnected) {
      clearTooltip(state)
      return
    }
    applyTooltip(state, cell)
  })
  observer.observe(cell, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: [
      'data-table-overflow-title',
      'data-table-overflow-title-always',
      'data-table-overflow-explicit-only',
    ],
  })
  state.contentObserver = observer
}

function showTooltip(state: OverflowTitleState, cell: HTMLElement, text: string) {
  if (activeState && activeState !== state) {
    clearTooltip(activeState)
  }

  const tooltip = ensureTooltip()
  state.active = cell
  activeState = state
  tooltip.textContent = text
  tooltip.style.display = 'block'
  cell.setAttribute('aria-describedby', TOOLTIP_ID)
  positionTooltip(cell)
  observeTooltipContent(state, cell)
}

function applyTooltip(state: OverflowTitleState, cell: HTMLElement) {
  const text = state.titleForTarget(cell)
  if (!text) {
    clearTooltip(state)
    return
  }

  if (state.active === cell) {
    if (tooltipElement?.textContent !== text) tooltipElement!.textContent = text
    positionTooltip(cell)
    return
  }
  clearTooltip(state)
  showTooltip(state, cell, text)
}

function clearPendingTooltip(state: OverflowTitleState) {
  if (state.pendingTimer !== null) {
    window.clearTimeout(state.pendingTimer)
    state.pendingTimer = null
  }
  state.pending = null
}

function clearPendingHide(state: OverflowTitleState) {
  if (state.hideTimer !== null) {
    window.clearTimeout(state.hideTimer)
    state.hideTimer = null
  }
}

function scheduleTooltipHide(state: OverflowTitleState) {
  clearPendingHide(state)
  state.hideTimer = window.setTimeout(() => {
    state.hideTimer = null
    clearTooltip(state)
  }, TOOLTIP_HIDE_AFTER_MS)
}

function scheduleTooltip(state: OverflowTitleState, cell: HTMLElement) {
  clearPendingHide(state)
  if (state.active === cell) {
    applyTooltip(state, cell)
    return
  }
  if (state.pending === cell) return

  clearPendingTooltip(state)
  clearTooltip(state)
  state.pending = cell
  state.pendingTimer = window.setTimeout(() => {
    state.pendingTimer = null
    if (state.pending !== cell) return
    state.pending = null
    applyTooltip(state, cell)
  }, TOOLTIP_SHOW_AFTER_MS)
}

function clearTooltip(state: OverflowTitleState) {
  clearPendingHide(state)
  stopObservingTooltipContent(state)
  if (!state.active) return
  state.active.removeAttribute('aria-describedby')
  state.active = null
  state.tooltipHovered = false
  if (activeState === state) {
    activeState = null
    if (tooltipElement) tooltipElement.style.display = 'none'
  }
}

function installWithResolvers(
  el: HTMLElement,
  resolveTarget: (eventTarget: EventTarget | null) => HTMLElement | null,
  titleForTarget: (target: HTMLElement) => string,
) {
  if (stateByElement.has(el)) return

  const state: OverflowTitleState = {
    active: null,
    pending: null,
    pendingTimer: null,
    hideTimer: null,
    contentObserver: null,
    tooltipHovered: false,
    resolveTarget,
    titleForTarget,
    onMouseOver: (event) => {
      const cell = state.resolveTarget(event.target)
      if (cell) scheduleTooltip(state, cell)
    },
    onMouseOut: (event) => {
      const relatedTarget = event.relatedTarget instanceof Node ? event.relatedTarget : null
      if (state.pending && relatedTarget && state.pending.contains(relatedTarget)) return
      if (state.pending) clearPendingTooltip(state)
      if (!state.active || (relatedTarget && state.active.contains(relatedTarget))) return
      if (relatedTarget === tooltipElement || tooltipElement?.contains(relatedTarget)) {
        clearPendingHide(state)
        return
      }
      scheduleTooltipHide(state)
    },
    onFocusIn: (event) => {
      const cell = state.resolveTarget(event.target)
      if (cell) scheduleTooltip(state, cell)
    },
    onFocusOut: () => {
      clearPendingTooltip(state)
      if (!state.tooltipHovered) clearTooltip(state)
    },
    onScroll: () => {
      clearPendingTooltip(state)
      if (state.active) positionTooltip(state.active)
    },
    onResize: () => {
      clearPendingTooltip(state)
      if (state.active) positionTooltip(state.active)
    },
    onColumnResize: () => {
      clearPendingTooltip(state)
      if (state.active) applyTooltip(state, state.active)
    },
  }

  el.addEventListener('mouseover', state.onMouseOver)
  el.addEventListener('mouseout', state.onMouseOut)
  el.addEventListener('focusin', state.onFocusIn)
  el.addEventListener('focusout', state.onFocusOut)
  window.addEventListener('scroll', state.onScroll, true)
  window.addEventListener('resize', state.onResize)
  el.addEventListener(COLUMN_RESIZED_EVENT, state.onColumnResize)
  stateByElement.set(el, state)
}

function install(el: HTMLElement) {
  installWithResolvers(
    el,
    (eventTarget) => findCell(el, eventTarget),
    titleTextForCell,
  )
}

function installOverflowTitle(el: HTMLElement) {
  installWithResolvers(
    el,
    (eventTarget) => eventTarget instanceof Node && el.contains(eventTarget) ? el : null,
    (target) => {
      if (!isOverflowing(target)) return ''
      return target.dataset.overflowTitle?.trim() || textFromElement(target)
    },
  )
}

function uninstall(el: HTMLElement) {
  const state = stateByElement.get(el)
  if (!state) return
  clearPendingTooltip(state)
  clearPendingHide(state)
  clearTooltip(state)
  el.removeEventListener('mouseover', state.onMouseOver)
  el.removeEventListener('mouseout', state.onMouseOut)
  el.removeEventListener('focusin', state.onFocusIn)
  el.removeEventListener('focusout', state.onFocusOut)
  window.removeEventListener('scroll', state.onScroll, true)
  window.removeEventListener('resize', state.onResize)
  el.removeEventListener(COLUMN_RESIZED_EVENT, state.onColumnResize)
  stateByElement.delete(el)
}

function installListTables(root: ParentNode) {
  if (root instanceof HTMLElement && root.matches('.el-table.hfl-list-table')) {
    install(root)
  }
  root.querySelectorAll?.<HTMLElement>('.el-table.hfl-list-table').forEach(install)
}

function setupAutoInstall() {
  if (autoInstallStarted || typeof window === 'undefined') return
  autoInstallStarted = true

  const start = () => {
    installListTables(document)
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of Array.from(mutation.addedNodes)) {
          if (node instanceof HTMLElement) installListTables(node)
        }
      }
    })
    observer.observe(document.body, { childList: true, subtree: true })
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true })
  } else {
    queueMicrotask(start)
  }
}

export function setupTableOverflowTitleDirective(app: App) {
  app.directive('table-overflow-title', {
    mounted: install,
    unmounted: uninstall,
  })
  app.directive('overflow-title', {
    mounted(el: HTMLElement, binding) {
      el.dataset.overflowTitle = String(binding.value || '')
      installOverflowTitle(el)
    },
    updated(el: HTMLElement, binding) {
      el.dataset.overflowTitle = String(binding.value || '')
    },
    unmounted(el: HTMLElement) {
      uninstall(el)
      delete el.dataset.overflowTitle
    },
  })
  setupAutoInstall()
}
